from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from backend.schemas import (
    ArtifactDetail,
    ArtifactSummary,
    ConstraintDatasetSummary,
    DatasetPayload,
    PolicyDocumentSummary,
    WorkspaceSummary,
)
from backend.policy_report import extract_policy_text, load_policy_references
from boundary_agent.io import parse_json
from boundary_agent.llm.client import get_default_model_id, list_model_options
from boundary_agent.paths import EXPORTS_DIR, PROJECT_ROOT, SAMPLE_DATA_FILE
from report.report_service import generate_planning_policy_report
from boundary_agent.config import load_config
from boundary_agent.tools.trained_model import model_status, resolve_model_dir
from boundary_agent.spatial import (
    SpatialDataset,
    apply_constraint_layers,
    build_analysis_geojson,
    export_candidate_shapefile,
)


def _model_diagnostics() -> dict[str, Any]:
    config = load_config()
    status = model_status(config)
    model_dir = resolve_model_dir(config)
    shap_path = model_dir.parent / "outputs" / "shap_importance.csv"
    shap_top: list[dict[str, Any]] = []
    if shap_path.exists():
        with shap_path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    value = float(row.get("mean_abs_shap") or 0)
                except ValueError:
                    continue
                shap_top.append({
                    "feature": row.get("feature") or "",
                    "mean_abs_shap": value,
                })
                if len(shap_top) >= 5:
                    break
    status["shap_available"] = bool(shap_top)
    status["shap_top"] = shap_top
    return status


@dataclass(slots=True)
class StoredArtifact:
    summary: ArtifactSummary
    preview: Any
    content: bytes | None = None
    file_path: Path | None = None


_CONSTRAINT_LAYER_LABELS = {
    "ecological_redline": "生态保护红线",
    "farmland_permanent_basic_farmland": "永久基本农田",
    "urban_development_boundary": "城镇开发边界",
}


def _label_constraint_layer(path: str) -> str:
    stem = Path(path).stem
    return _CONSTRAINT_LAYER_LABELS.get(stem.lower(), stem)


def _inspect_constraint_dataset(name: str, source_path: Path) -> ConstraintDatasetSummary:
    suffix = source_path.suffix.lower()
    warnings: list[str] = []
    layers: list[str] = []

    if suffix == ".zip":
        with zipfile.ZipFile(source_path) as archive:
            names = archive.namelist()
        normalized = {item.replace("\\", "/").lower() for item in names}
        spatial_files = [
            item
            for item in names
            if item.lower().endswith((".shp", ".geojson", ".json"))
        ]
        for item in spatial_files:
            layers.append(_label_constraint_layer(item))
            if item.lower().endswith(".shp"):
                base = item.replace("\\", "/")[:-4].lower()
                missing = [
                    ext
                    for ext in (".dbf", ".shx")
                    if f"{base}{ext}" not in normalized
                ]
                if missing:
                    warnings.append(f"{Path(item).name} 缺少配套文件：{', '.join(missing)}")
    elif suffix in {".json", ".geojson"}:
        layers.append(_label_constraint_layer(name))
    else:
        warnings.append("当前仅支持 ZIP、JSON 或 GeoJSON 约束数据")

    unique_layers = list(dict.fromkeys(layers))
    if not unique_layers:
        warnings.append("未识别到可用的 SHP、GeoJSON 或 JSON 约束图层")

    return ConstraintDatasetSummary(
        loaded=bool(unique_layers),
        name=name,
        layer_count=len(unique_layers),
        layers=unique_layers,
        uploaded_at=datetime.now().isoformat(timespec="seconds"),
        warnings=warnings,
    )


class DemoWorkspace:
    """Single-user in-memory workspace suitable for a local demonstration."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._artifacts: dict[str, StoredArtifact] = {}
        self.dataset_name = "内置示例 blocks.json"
        self.blocks = parse_json(SAMPLE_DATA_FILE.read_bytes())
        self.spatial_dataset: SpatialDataset | None = None
        self.uploaded_path: Path | None = None
        self.constraint_path: Path | None = None
        self.constraint_dataset = ConstraintDatasetSummary()
        self.policy_documents: list[dict[str, Any]] = load_policy_references()
        self.latest_analysis_payload: dict[str, Any] | None = None

    def _clear_spatial_input(self) -> None:
        if self.spatial_dataset is not None:
            self.spatial_dataset.close()
            self.spatial_dataset = None
        if self.uploaded_path is not None:
            self.uploaded_path.unlink(missing_ok=True)
            self.uploaded_path = None

    def _clear_constraint_input(self) -> None:
        if self.constraint_path is not None:
            self.constraint_path.unlink(missing_ok=True)
            self.constraint_path = None
        self.constraint_dataset = ConstraintDatasetSummary()

    def _clear_policy_documents(self) -> None:
        for item in self.policy_documents:
            if item.get("builtin"):
                continue
            path = item.get("path")
            if isinstance(path, Path):
                path.unlink(missing_ok=True)
        self.policy_documents = load_policy_references()

    def reset(self) -> None:
        with self._lock:
            self._clear_spatial_input()
            self._clear_constraint_input()
            self._clear_policy_documents()
            self.dataset_name = "内置示例 blocks.json"
            self.blocks = parse_json(SAMPLE_DATA_FILE.read_bytes())
            self.latest_analysis_payload = None
            self._artifacts.clear()

    def set_dataset(self, name: str, blocks: list[dict[str, Any]], source: bytes, mime: str) -> ArtifactSummary:
        with self._lock:
            self._clear_spatial_input()
            self._artifacts.clear()
            self.dataset_name = name
            self.blocks = blocks
            return self._add_artifact(
                name,
                "dataset",
                mime,
                preview=blocks,
                content=source,
            )

    def set_spatial_dataset(self, dataset: SpatialDataset, source_path: Path) -> ArtifactSummary:
        with self._lock:
            self._clear_spatial_input()
            self._artifacts.clear()
            if self.constraint_path is not None:
                applied = apply_constraint_layers(dataset, self.constraint_path)
                if applied:
                    self.constraint_dataset.warnings = [
                        warning
                        for warning in self.constraint_dataset.warnings
                        if not warning.startswith("已完成空间叠加")
                    ]
                    self.constraint_dataset.warnings.append(
                        f"已完成空间叠加：{sum(applied.values())} 次约束命中。"
                    )
            self.spatial_dataset = dataset
            self.uploaded_path = source_path
            self.dataset_name = dataset.name
            self.blocks = dataset.blocks
            preview = {
                "spatial": build_analysis_geojson(dataset),
                "source_crs": dataset.source_crs,
            }
            return self._add_artifact(
                dataset.name,
                "spatial",
                "application/zip",
                preview,
                file_path=source_path,
            )

    def set_constraint_dataset(self, name: str, source_path: Path) -> ConstraintDatasetSummary:
        with self._lock:
            self._clear_constraint_input()
            self.constraint_path = source_path
            self.constraint_dataset = _inspect_constraint_dataset(name, source_path)
            if self.spatial_dataset is not None:
                applied = apply_constraint_layers(self.spatial_dataset, source_path)
                self.blocks = self.spatial_dataset.blocks
                if applied:
                    self.constraint_dataset.warnings.append(
                        f"已完成空间叠加：{sum(applied.values())} 次约束命中。"
                    )
                else:
                    self.constraint_dataset.warnings.append("未找到可用于空间叠加的标准三线图层。")
            return self.constraint_dataset

    def add_policy_document(self, name: str, source_path: Path, size: int) -> PolicyDocumentSummary:
        with self._lock:
            text, warning = extract_policy_text(source_path)
            summary = PolicyDocumentSummary(
                id=uuid4().hex,
                name=name,
                size=size,
                uploaded_at=datetime.now().isoformat(timespec="seconds"),
                text_length=len(text),
                excerpt=text[:140],
                warning=warning,
            )
            self.policy_documents.append({
                **summary.model_dump(),
                "path": source_path,
                "text": text,
                "builtin": False,
            })
            return summary

    def list_policy_document_summaries(self) -> list[PolicyDocumentSummary]:
        return [
            PolicyDocumentSummary(
                id=item["id"],
                name=item["name"],
                size=item["size"],
                uploaded_at=item["uploaded_at"],
                text_length=item.get("text_length", 0),
                excerpt=item.get("excerpt", ""),
                warning=item.get("warning"),
            )
            for item in self.policy_documents
        ]

    def add_analysis(self, payload: dict[str, Any]) -> ArtifactSummary:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with self._lock:
            self.latest_analysis_payload = dict(payload)
            preview = dict(payload)
            if self.spatial_dataset is not None:
                preview["spatial"] = build_analysis_geojson(self.spatial_dataset, payload)
            analysis = self._add_artifact(
                f"analysis_{timestamp}.json",
                "analysis",
                "application/json",
                preview=preview,
                content=content,
            )
            if self.spatial_dataset is not None:
                exported = export_candidate_shapefile(
                    self.spatial_dataset,
                    payload.get("decisions", []),
                    EXPORTS_DIR / f"candidate_adjustment_blocks_{timestamp}.shp.zip",
                )
                if exported is not None:
                    name, spatial_content = exported
                    spatial_path = spatial_content if isinstance(spatial_content, Path) else None
                    self._add_artifact(
                        name,
                        "spatial",
                        "application/zip",
                        preview,
                        content=spatial_content if isinstance(spatial_content, bytes) else None,
                        file_path=spatial_path,
                    )
                inclusion_exported = export_candidate_shapefile(
                    self.spatial_dataset,
                    payload.get("decisions", []),
                    EXPORTS_DIR / f"candidate_inclusion_blocks_{timestamp}.shp.zip",
                    decision_types={"一级优先调入", "二级优先调入"},
                    output_name="candidate_inclusion_blocks",
                )
                if inclusion_exported is not None:
                    name, spatial_content = inclusion_exported
                    spatial_path = spatial_content if isinstance(spatial_content, Path) else None
                    self._add_artifact(
                        name,
                        "spatial",
                        "application/zip",
                        preview,
                        content=spatial_content if isinstance(spatial_content, bytes) else None,
                        file_path=spatial_path,
                    )
            return analysis

    def add_policy_report_pdf(self, model_settings: dict[str, Any] | None) -> ArtifactSummary:
        if self.latest_analysis_payload is None:
            raise ValueError("缺少候选地块分析结果")
        spatial_geojson = (
            build_analysis_geojson(self.spatial_dataset, self.latest_analysis_payload)
            if self.spatial_dataset is not None
            else None
        )
        outputs = generate_planning_policy_report(
            self.latest_analysis_payload,
            self.policy_documents,
            self.dataset_name,
            model_settings,
            PROJECT_ROOT / "outputs",
            spatial_geojson=spatial_geojson,
        )
        preview = {
            "type": "policy_report",
            "title": outputs.title,
            "text": outputs.text,
            "model_used": outputs.model_used,
            "warning": outputs.warning,
            "evidence": outputs.evidence,
            "policy_documents": [item.model_dump() for item in self.list_policy_document_summaries()],
        }
        docx_summary = self._add_artifact(
            outputs.docx_path.name,
            "report",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            preview=preview,
            file_path=outputs.docx_path,
        )
        preview["docx_artifact_id"] = docx_summary.id
        return self._add_artifact(
            outputs.pdf_path.name,
            "report",
            "application/pdf",
            preview=preview,
            file_path=outputs.pdf_path,
        )

    def _add_artifact(
        self,
        name: str,
        kind: str,
        mime: str,
        preview: Any,
        content: bytes | None = None,
        file_path: Path | None = None,
    ) -> ArtifactSummary:
        if content is None and file_path is None:
            raise ValueError("Artifact requires content or file_path")
        artifact_id = uuid4().hex
        size = len(content) if content is not None else file_path.stat().st_size
        summary = ArtifactSummary(
            id=artifact_id,
            name=name,
            kind=kind,
            mime=mime,
            size=size,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._artifacts[artifact_id] = StoredArtifact(
            summary=summary,
            preview=preview,
            content=content,
            file_path=file_path,
        )
        return summary

    def list_artifacts(self) -> list[ArtifactSummary]:
        with self._lock:
            return [item.summary for item in reversed(self._artifacts.values())]

    def get_artifact(self, artifact_id: str) -> StoredArtifact | None:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def get_artifact_detail(self, artifact_id: str) -> ArtifactDetail | None:
        item = self.get_artifact(artifact_id)
        if item is None:
            return None
        return ArtifactDetail(**item.summary.model_dump(), preview=item.preview)

    def summary(self) -> WorkspaceSummary:
        with self._lock:
            fields = {key for block in self.blocks for key in block}
            models = list_model_options()
            return WorkspaceSummary(
                dataset_name=self.dataset_name,
                block_count=len(self.blocks),
                field_count=len(fields),
                has_spatial_data=self.spatial_dataset is not None,
                dataset_warnings=self.spatial_dataset.warnings if self.spatial_dataset else [],
                constraint_dataset=self.constraint_dataset,
                policy_documents=self.list_policy_document_summaries(),
                model_diagnostics=_model_diagnostics(),
                model_ready=any(item["kind"] == "llm" and item["ready"] for item in models),
                default_model_id=get_default_model_id(),
                models=models,
                artifacts=self.list_artifacts(),
            )

    def dataset_payload(self, offset: int = 0, limit: int = 50, query: str | None = None) -> DatasetPayload:
        with self._lock:
            blocks = self.blocks
            text = (query or "").strip().lower()
            if text:
                blocks = [
                    block
                    for block in blocks
                    if text in str(block.get("block_id", "")).lower()
                    or text in str(block.get("block_name") or "").lower()
                    or any(text in str(value).lower() for value in block.values())
                ]
            page = blocks[offset : offset + limit]
            return DatasetPayload(
                name=self.dataset_name,
                warnings=self.spatial_dataset.warnings if self.spatial_dataset else [],
                block_count=len(self.blocks),
                matched_count=len(blocks),
                offset=offset,
                limit=limit,
                has_more=offset + len(page) < len(blocks),
                blocks=list(page),
            )
