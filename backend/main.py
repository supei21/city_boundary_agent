from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.schemas import (
    ArtifactDetail,
    ArtifactSummary,
    CandidateParcelPayload,
    CandidatePolicyAnalysisRequest,
    CandidatePolicyAnalysisResponse,
    ChatRequest,
    CreateUserModelResponse,
    DatasetPayload,
    LandscapeEvaluateRequest,
    LandscapeScenarioEvaluateRequest,
    RagPolicyDocumentDetail,
    RagPolicyDocumentSummary,
    RagPolicySearchResult,
    RagQuestionRequest,
    RagQuestionResponse,
    PolicyFeasibilityRequest,
    PolicyFeasibilityResponse,
    PolicyBatchValidationRequest,
    PolicyBatchValidationResponse,
    ReportGenerateRequest,
    UserModelInput,
    UserModelOutput,
    WorkspaceSummary,
)
from backend.policy_report import POLICY_REFERENCE_PDF
from backend.workspace import DemoWorkspace
from backend.uploads import UploadTooLargeError, persist_upload
from boundary_agent.agent.events import AgentEvent
from boundary_agent.io import parse_csv, parse_json
from boundary_agent.llm.chat_service import handle_chat
from boundary_agent.llm.client import get_model_settings, list_model_options
from boundary_agent.llm.user_models import (
    UserModelConflictError,
    add_user_model,
    delete_user_model,
)
from boundary_agent.config import load_config
from boundary_agent.rag.config import settings_from_config as rag_settings_from_config
from boundary_agent.rag.library import get_policy_document, list_policy_library, search_policy_library
from boundary_agent.rag.policy_service import (
    answer_policy_question,
    analyze_candidate_policy_feasibility,
    analyze_policy_feasibility,
    validate_policy_feasibility_batch,
)
from boundary_agent.spatial import (
    read_arcgis_feature_set,
    read_landscape_boundary_from_constraint_zip,
    read_shapefile_zip_path,
)


app = FastAPI(
    title="城镇开发边界智能体 Demo API",
    version="2.0.0-demo",
    description="ReAct工具调用、确定性规则分析与成果文件预览接口。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

workspace = DemoWorkspace()
_STREAM_END = object()
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _stream_text_chunks(text: str, size: int = 24) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= size or char in {"\n", "。", "；", "，"}:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


@app.get("/api/health")
def health() -> dict[str, Any]:
    summary = workspace.summary()
    return {
        "status": "ok",
        "version": app.version,
        "model_ready": summary.model_ready,
        "block_count": summary.block_count,
    }


@app.get("/api/workspace", response_model=WorkspaceSummary)
def get_workspace() -> WorkspaceSummary:
    return workspace.summary()


@app.get("/api/dataset", response_model=DatasetPayload)
def get_dataset(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=120),
) -> DatasetPayload:
    return workspace.dataset_payload(offset=offset, limit=limit, query=q)


@app.get("/api/candidates", response_model=CandidateParcelPayload)
def get_candidate_parcels() -> CandidateParcelPayload:
    return workspace.candidate_parcels()


@app.post("/api/workspace/reset", response_model=WorkspaceSummary)
def reset_workspace() -> WorkspaceSummary:
    workspace.reset()
    return workspace.summary()


@app.post("/api/models", response_model=CreateUserModelResponse)
def create_user_model(payload: UserModelInput) -> CreateUserModelResponse:
    try:
        record = add_user_model(payload.label, payload.model, payload.base_url, payload.api_key)
    except UserModelConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CreateUserModelResponse(
        model=UserModelOutput(
            id=record["id"],
            label=record["label"],
            model=record["model"],
            base_url=record["base_url"],
            created_at=record["created_at"],
            api_key_masked=f"••••{record['api_key'][-4:]}",
        ),
        workspace=workspace.summary(),
    )


@app.delete("/api/models/{model_id}", response_model=WorkspaceSummary)
def remove_user_model(model_id: str) -> WorkspaceSummary:
    if not model_id.startswith("custom-"):
        raise HTTPException(status_code=400, detail="只能删除自定义模型")
    if not delete_user_model(model_id):
        raise HTTPException(status_code=404, detail="自定义模型不存在")
    return workspace.summary()


@app.post("/api/datasets/upload", response_model=WorkspaceSummary)
async def upload_dataset(file: UploadFile = File(...)) -> WorkspaceSummary:
    name = file.filename or "uploaded-data"
    suffix = name.rsplit(".", maxsplit=1)[-1].lower() if "." in name else ""
    if suffix not in {"json", "csv", "zip"}:
        raise HTTPException(status_code=400, detail="当前Demo支持JSON、CSV或SHP ZIP")
    try:
        if suffix == "zip":
            upload_path, _ = await persist_upload(file, name)
            try:
                dataset = read_shapefile_zip_path(name, upload_path)
            except Exception:
                upload_path.unlink(missing_ok=True)
                raise
            workspace.set_spatial_dataset(dataset, upload_path)
            return workspace.summary()
        content = await file.read()
        if suffix == "json":
            parsed = json.loads(content.decode("utf-8-sig"))
            if isinstance(parsed, dict) and isinstance(parsed.get("features"), list) and parsed.get("geometryType"):
                dataset = read_arcgis_feature_set(name, content)
                workspace.set_spatial_dataset(dataset, dataset.source_path)
                return workspace.summary()
            blocks = parse_json(content)
        else:
            blocks = parse_csv(content)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=f"数据解析失败：{exc}") from exc
    mime = "application/json" if suffix == "json" else "text/csv"
    workspace.set_dataset(name, blocks, content, mime)
    return workspace.summary()


@app.post("/api/constraints/upload", response_model=WorkspaceSummary)
async def upload_constraint_dataset(file: UploadFile = File(...)) -> WorkspaceSummary:
    name = file.filename or "constraint-data"
    suffix = name.rsplit(".", maxsplit=1)[-1].lower() if "." in name else ""
    if suffix not in {"zip", "json", "geojson"}:
        raise HTTPException(status_code=400, detail="约束数据当前支持SHP ZIP、JSON或GeoJSON")
    upload_path: Path | None = None
    try:
        upload_path, _ = await persist_upload(file, name)
        landscape_boundary = (
            read_landscape_boundary_from_constraint_zip(name, upload_path)
            if suffix == "zip"
            else None
        )
        workspace.set_constraint_dataset(name, upload_path, landscape_boundary)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ValueError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"约束数据解析失败：{exc}") from exc
    return workspace.summary()


@app.get("/api/landscape/boundary/geojson")
def get_landscape_boundary_geojson() -> dict[str, Any]:
    try:
        return workspace.landscape_boundary_geojson()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/landscape/evaluate")
def evaluate_landscape(payload: LandscapeEvaluateRequest) -> dict[str, Any]:
    try:
        return workspace.evaluate_landscape(
            block_ids=payload.block_ids,
            block_actions=payload.block_actions,
            action=payload.action,
            neighborhood_m=payload.neighborhood_m,
            cell_size_m=payload.cell_size_m,
            connectivity=payload.connectivity,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/landscape/evaluate/stream")
async def stream_landscape_evaluation(payload: LandscapeEvaluateRequest) -> StreamingResponse:
    event_queue: Queue[object] = Queue()

    def publish(progress: dict[str, Any]) -> None:
        event_queue.put({"type": "progress", "progress": progress})

    def run() -> None:
        try:
            result = workspace.evaluate_landscape(
                block_ids=payload.block_ids,
                block_actions=payload.block_actions,
                action=payload.action,
                neighborhood_m=payload.neighborhood_m,
                cell_size_m=payload.cell_size_m,
                connectivity=payload.connectivity,
                limit=payload.limit,
                progress_callback=publish,
            )
            event_queue.put({"type": "result", "result": jsonable_encoder(result)})
        except Exception as exc:
            event_queue.put({"type": "error", "message": f"边界形态评价失败：{exc}"})
        finally:
            event_queue.put(_STREAM_END)

    Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            item = await asyncio.to_thread(event_queue.get)
            if item is _STREAM_END:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/landscape/scenario/evaluate")
def evaluate_landscape_scenario(payload: LandscapeScenarioEvaluateRequest) -> dict[str, Any]:
    try:
        return workspace.evaluate_landscape_scenario(
            block_ids=payload.block_ids,
            action=payload.action,
            neighborhood_m=payload.neighborhood_m,
            cell_size_m=payload.cell_size_m,
            connectivity=payload.connectivity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/policies/upload", response_model=WorkspaceSummary)
async def upload_policy_documents(files: list[UploadFile] = File(...)) -> WorkspaceSummary:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个政策文件")
    for file in files:
        name = file.filename or "policy-document"
        suffix = name.rsplit(".", maxsplit=1)[-1].lower() if "." in name else ""
        if suffix not in {"doc", "docx", "txt"}:
            raise HTTPException(status_code=400, detail="政策文件当前支持DOC、DOCX或TXT")
        try:
            upload_path, size = await persist_upload(file, name)
            workspace.add_policy_document(name, upload_path, size)
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (ValueError, TypeError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=422, detail=f"政策文件解析失败：{exc}") from exc
    return workspace.summary()


@app.post("/api/reports/policy-pdf", response_model=ArtifactSummary)
def generate_policy_report(payload: ReportGenerateRequest) -> ArtifactSummary:
    if workspace.latest_analysis_payload is None:
        raise HTTPException(status_code=409, detail="请先完成一次候选地块分析，再生成政策PDF")
    try:
        model_settings = get_model_settings(payload.model_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workspace.add_policy_report_pdf(model_settings)


@app.get("/api/policies/reference-pdf/preview")
def preview_policy_reference_pdf() -> FileResponse:
    if not POLICY_REFERENCE_PDF.exists():
        raise HTTPException(status_code=404, detail="政策参考PDF不存在")
    return FileResponse(
        POLICY_REFERENCE_PDF,
        media_type="application/pdf",
        filename=POLICY_REFERENCE_PDF.name,
        content_disposition_type="inline",
    )


@app.get("/api/rag/policies", response_model=list[RagPolicyDocumentSummary])
def list_rag_policy_documents() -> list[RagPolicyDocumentSummary]:
    settings = rag_settings_from_config(load_config())
    return [RagPolicyDocumentSummary(**item) for item in list_policy_library(settings)]


@app.get("/api/rag/policies/{document_id}", response_model=RagPolicyDocumentDetail)
def get_rag_policy_document(document_id: str) -> RagPolicyDocumentDetail:
    settings = rag_settings_from_config(load_config())
    document = get_policy_document(settings, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="RAG政策文档不存在")
    return RagPolicyDocumentDetail(**document)


@app.get("/api/rag/search", response_model=list[RagPolicySearchResult])
def search_rag_policies(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=8, ge=1, le=30),
) -> list[RagPolicySearchResult]:
    settings = rag_settings_from_config(load_config())
    return [RagPolicySearchResult(**item) for item in search_policy_library(settings, q, limit)]


@app.post("/api/rag/ask", response_model=RagQuestionResponse)
def ask_rag_question(payload: RagQuestionRequest) -> RagQuestionResponse:
    settings = rag_settings_from_config(load_config())
    try:
        model_settings = get_model_settings(payload.model_id)
        result = answer_policy_question(payload.question, settings, model_settings)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RagQuestionResponse(**result)


@app.post("/api/rag/feasibility", response_model=PolicyFeasibilityResponse)
def analyze_rag_feasibility(payload: PolicyFeasibilityRequest) -> PolicyFeasibilityResponse:
    settings = rag_settings_from_config(load_config())
    try:
        model_settings = get_model_settings(payload.model_id)
        parcel = workspace.enrich_parcel_attributes(payload.parcel)
        result = analyze_policy_feasibility(parcel, settings, model_settings)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PolicyFeasibilityResponse(**result)


@app.post("/api/rag/feasibility/batch", response_model=PolicyBatchValidationResponse)
def validate_rag_feasibility_batch(payload: PolicyBatchValidationRequest) -> PolicyBatchValidationResponse:
    settings = rag_settings_from_config(load_config())
    try:
        model_settings = get_model_settings(payload.model_id)
        enriched_cases = []
        for raw_case in payload.test_cases:
            case = dict(raw_case)
            key = "parcel" if isinstance(case.get("parcel"), dict) else "land_attrs"
            parcel = case.get(key) if isinstance(case.get(key), dict) else {}
            case[key] = workspace.enrich_parcel_attributes(parcel)
            enriched_cases.append(case)
        result = validate_policy_feasibility_batch(enriched_cases, settings, model_settings)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PolicyBatchValidationResponse(**result)


@app.post("/api/rag/candidates/feasibility", response_model=CandidatePolicyAnalysisResponse)
def analyze_candidate_feasibility(payload: CandidatePolicyAnalysisRequest) -> CandidatePolicyAnalysisResponse:
    settings = rag_settings_from_config(load_config())
    candidates = workspace.candidate_parcels()
    if candidates.analysis_required or not candidates.parcels:
        raise HTTPException(status_code=422, detail="当前没有候选地块，请先运行候选地块分析。")
    try:
        model_settings = get_model_settings(payload.model_id)
        result = analyze_candidate_policy_feasibility(
            candidates.parcels,
            settings,
            model_settings,
        )
        decisions, spatial, artifact = workspace.apply_candidate_policy_results(
            result["results"],
            result["report"],
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CandidatePolicyAnalysisResponse(
        **result,
        decisions=decisions,
        spatial=spatial,
        artifact=artifact,
    )


@app.post("/api/rag/candidates/feasibility/stream")
async def stream_candidate_policy_feasibility(
    payload: CandidatePolicyAnalysisRequest,
) -> StreamingResponse:
    settings = rag_settings_from_config(load_config())
    candidates = workspace.candidate_parcels()
    if candidates.analysis_required or not candidates.parcels:
        raise HTTPException(status_code=422, detail="当前没有候选地块，请先运行候选地块分析。")
    try:
        model_settings = get_model_settings(payload.model_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    event_queue: Queue[object] = Queue()

    def publish(progress: dict[str, Any]) -> None:
        event_queue.put({"type": "progress", "progress": progress})

    def run() -> None:
        try:
            result = analyze_candidate_policy_feasibility(
                candidates.parcels,
                settings,
                model_settings,
                progress_callback=publish,
            )
            decisions, spatial, artifact = workspace.apply_candidate_policy_results(
                result["results"],
                result["report"],
            )
            response = CandidatePolicyAnalysisResponse(
                **result,
                decisions=decisions,
                spatial=spatial,
                artifact=artifact,
            )
            event_queue.put({"type": "result", "result": jsonable_encoder(response)})
        except Exception as exc:
            event_queue.put({"type": "error", "message": f"候选地块政策可行性分析失败：{exc}"})
        finally:
            event_queue.put(_STREAM_END)

    Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            item = await asyncio.to_thread(event_queue.get)
            if item is _STREAM_END:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/artifacts", response_model=list[ArtifactSummary])
def list_artifacts() -> list[ArtifactSummary]:
    return workspace.list_artifacts()


@app.get("/api/artifacts/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: str) -> ArtifactDetail:
    artifact = workspace.get_artifact_detail(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="成果文件不存在")
    return artifact


@app.get("/api/artifacts/{artifact_id}/preview")
def preview_artifact(artifact_id: str) -> Response:
    artifact = workspace.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="成果文件不存在")
    if artifact.file_path is not None:
        return FileResponse(
            artifact.file_path,
            media_type=artifact.summary.mime,
            filename=artifact.summary.name,
            content_disposition_type="inline",
        )
    if artifact.content is None:
        raise HTTPException(status_code=404, detail="成果文件内容不存在")
    return Response(artifact.content, media_type=artifact.summary.mime)


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str) -> Response:
    artifact = workspace.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="成果文件不存在")
    if artifact.file_path is not None:
        return FileResponse(
            artifact.file_path,
            media_type=artifact.summary.mime,
            filename=artifact.summary.name,
        )
    if artifact.content is None:
        raise HTTPException(status_code=404, detail="成果文件内容不存在")
    headers = {"Content-Disposition": f'attachment; filename="{artifact.summary.name}"'}
    return Response(artifact.content, media_type=artifact.summary.mime, headers=headers)


@app.post("/api/chat/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    """Stream public agent events and the final structured result as NDJSON."""

    try:
        model_settings = get_model_settings(request.model_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    model_option = next(
        (item for item in list_model_options() if item["id"] == request.model_id),
        None,
    )
    if model_option is None or not model_option["ready"]:
        raise HTTPException(status_code=422, detail="所选模型尚未完成配置")

    event_queue: Queue[object] = Queue()
    blocks_snapshot = list(workspace.blocks)

    def publish(event: AgentEvent) -> None:
        event_queue.put({"type": "process", "event": event.model_dump()})

    def evaluate_landscape_with_progress(**kwargs: Any) -> dict[str, Any]:
        kwargs["progress_callback"] = lambda progress: event_queue.put({
            "type": "landscape_progress",
            "progress": progress,
        })
        return workspace.evaluate_landscape(**kwargs)

    def run() -> None:
        try:
            result = handle_chat(
                request.query,
                blocks_snapshot,
                settings=model_settings,
                event_callback=publish,
                landscape_evaluator=evaluate_landscape_with_progress,
                latest_analysis_provider=workspace.get_latest_analysis_payload,
            )
            payload = result.model_dump(mode="json")
            payload["model_id"] = request.model_id
            payload["model_name"] = model_option["label"]
            analysis_artifact = None
            if payload["decisions"] or payload["block_records"]:
                analysis_artifact = workspace.add_analysis(payload)
            landscape_artifact = (payload.get("landscape_evaluation") or {}).get("artifact")
            artifact = (
                ArtifactSummary.model_validate(landscape_artifact)
                if isinstance(landscape_artifact, dict)
                else analysis_artifact
            )
            for chunk in _stream_text_chunks(str(payload.get("response") or "")):
                event_queue.put({"type": "answer_delta", "content": chunk})
            event_queue.put(
                {
                    "type": "result",
                    "result": payload,
                    "artifact": artifact.model_dump() if artifact else None,
                }
            )
        except Exception as exc:
            event_queue.put({"type": "error", "message": f"分析失败：{exc}"})
        finally:
            event_queue.put(_STREAM_END)

    Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            item = await asyncio.to_thread(event_queue.get)
            if item is _STREAM_END:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_fallback(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        return FileResponse(_FRONTEND_DIST / "index.html")
