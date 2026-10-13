from __future__ import annotations

import csv
import json
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import geopandas as gpd

from backend.schemas import (
    ArtifactDetail,
    ArtifactSummary,
    ConstraintDatasetSummary,
    DatasetPayload,
    LandscapeBoundarySummary,
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
    LandscapeBoundary,
    SpatialDataset,
    apply_constraint_layers,
    build_analysis_geojson,
    build_landscape_boundary_geojson,
    build_landscape_results_geojson,
    build_landscape_scenario_geojson,
    evaluate_landscape_change,
    evaluate_landscape_scenario,
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
    "urban_boundary": "城镇开发边界",
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
        self.landscape_boundary_path: Path | None = None
        self.landscape_boundary: LandscapeBoundary | None = None
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

    def _clear_landscape_boundary(self) -> None:
        if self.landscape_boundary is not None:
            self.landscape_boundary.close()
            self.landscape_boundary = None
        if self.landscape_boundary_path is not None:
            self.landscape_boundary_path.unlink(missing_ok=True)
            self.landscape_boundary_path = None

    def _clear_policy_documents(self) -> None:
        for item in self.policy_documents:
            if item.get("builtin"):
                continue
            path = item.get("path")
            if isinstance(path, Path):
                path.unlink(missing_ok=True)
        self.policy_documents = load_policy_references()

    def _apply_landscape_membership(self) -> None:
        """Refresh inside/outside flags from the single baseline boundary using area overlap."""
        if self.landscape_boundary is None or self.spatial_dataset is None:
            return
        frame = self.spatial_dataset.original
        boundary_geometry = gpd.GeoSeries(
            [self.landscape_boundary.geometry],
            crs=self.landscape_boundary.source_crs,
        ).to_crs(frame.crs).iloc[0]
        metric_crs = frame.crs if frame.crs.is_projected else frame.estimate_utm_crs()
        if metric_crs is None:
            raise ValueError("无法确定街区与开发边界叠加所需的米制坐标系")
        metric_frame = frame.to_crs(metric_crs)
        metric_boundary = gpd.GeoSeries(
            [boundary_geometry],
            crs=frame.crs,
        ).to_crs(metric_crs).iloc[0]
        denominator = metric_frame.geometry.area.replace(0, float("nan"))
        overlap_ratio = (
            metric_frame.geometry.intersection(metric_boundary).area / denominator
        ).fillna(0.0).clip(0.0, 1.0)
        inside = overlap_ratio.ge(0.5).astype(bool)
        frame["boundary_overlap_ratio"] = overlap_ratio.values
        frame["inside_boundary"] = inside.values
        for block, ratio, value in zip(
            self.spatial_dataset.blocks,
            overlap_ratio.tolist(),
            inside.tolist(),
            strict=True,
        ):
            block["boundary_overlap_ratio"] = float(ratio)
            block["inside_boundary"] = bool(value)
        self.blocks = self.spatial_dataset.blocks
        self.spatial_dataset.warnings = [
            warning
            for warning in self.spatial_dataset.warnings
            if "未检测到现状开发边界标识" not in warning
        ]
        note = "已依据街区与城镇开发边界的面积重叠比例更新边界内外标识；重叠达到50%判定为边界内。"
        if note not in self.spatial_dataset.warnings:
            self.spatial_dataset.warnings.append(note)

    def reset(self) -> None:
        with self._lock:
            self._clear_spatial_input()
            self._clear_constraint_input()
            self._clear_landscape_boundary()
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
            self._apply_landscape_membership()
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

    def set_constraint_dataset(
        self,
        name: str,
        source_path: Path,
        landscape_boundary: LandscapeBoundary | None = None,
    ) -> ConstraintDatasetSummary:
        with self._lock:
            self._clear_landscape_boundary()
            self._clear_constraint_input()
            self.constraint_path = source_path
            self.constraint_dataset = _inspect_constraint_dataset(name, source_path)
            self.landscape_boundary = landscape_boundary
            self.landscape_boundary_path = None
            if self.spatial_dataset is not None:
                applied = apply_constraint_layers(self.spatial_dataset, source_path)
                self.blocks = self.spatial_dataset.blocks
                if applied:
                    self.constraint_dataset.warnings.append(
                        f"已完成空间叠加：{sum(applied.values())} 次约束命中。"
                    )
                elif landscape_boundary is None:
                    self.constraint_dataset.warnings.append("未找到可用于空间叠加的标准三线图层。")
                if landscape_boundary is not None:
                    self._apply_landscape_membership()
            if landscape_boundary is None:
                self.constraint_dataset.warnings.append(
                    "未识别到城镇开发边界图层，调入调出边界判定与形态评价暂不可用。"
                )
            return self.constraint_dataset

    def set_landscape_boundary(
        self,
        boundary: LandscapeBoundary,
        source_path: Path,
    ) -> LandscapeBoundarySummary:
        with self._lock:
            self._clear_landscape_boundary()
            self.landscape_boundary = boundary
            self.landscape_boundary_path = source_path
            self._apply_landscape_membership()
            return LandscapeBoundarySummary.model_validate(boundary.summary())

    def landscape_boundary_geojson(self) -> dict[str, Any]:
        with self._lock:
            if self.landscape_boundary is None:
                raise ValueError("请先上传现状城镇开发边界")
            return build_landscape_boundary_geojson(self.landscape_boundary)

    def evaluate_landscape(
        self,
        block_ids: list[str],
        block_actions: dict[str, str],
        action: str,
        neighborhood_m: float | None,
        cell_size_m: float | None,
        connectivity: int | None,
        limit: int | None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.landscape_boundary is None:
                raise ValueError("请先上传现状城镇开发边界")
            if self.spatial_dataset is None:
                raise ValueError("请先上传包含空间几何的候选地块SHP ZIP")
            config = load_config().get("landscape", {})
            adjacency_tolerance_m = float(config.get("adjacency_tolerance_m", 300.0))
            selected_ids = [str(value) for value in block_ids if str(value).strip()]
            if not selected_ids:
                selected_ids = self.spatial_dataset.original["block_id"].astype(str).tolist()
            if limit is not None:
                selected_ids = selected_ids[:limit]
            if progress_callback is not None:
                progress_callback({
                    "phase": "preparing",
                    "status": "running",
                    "completed": 0,
                    "total": len(selected_ids),
                    "evaluated_count": 0,
                    "percent": 0.0,
                    "current_block_id": None,
                    "message": "正在准备开发边界和候选地块",
                })
            results = evaluate_landscape_change(
                self.landscape_boundary,
                self.spatial_dataset.original,
                selected_ids,
                block_actions={key: value for key, value in block_actions.items() if value in {"include", "exclude"}},
                default_action=action,
                neighborhood_m=float(neighborhood_m or config.get("neighborhood_m", 1500)),
                cell_size_m=float(cell_size_m or config.get("cell_size_m", 50)),
                connectivity=int(connectivity or config.get("connectivity", 8)),
                adjacency_tolerance_m=adjacency_tolerance_m,
                stable_relative_threshold=float(config.get("stable_relative_threshold", 0.0005)),
                significant_relative_threshold=float(config.get("significant_relative_threshold", 0.005)),
                stable_pd_relative_threshold=float(config.get("single_stable_pd_relative_threshold", 0.00005)),
                significant_pd_relative_threshold=float(config.get("single_significant_pd_relative_threshold", 0.001)),
                stable_percentage_point_threshold=float(config.get("stable_percentage_point_threshold", 0.01)),
                significant_percentage_point_threshold=float(config.get("significant_percentage_point_threshold", 0.1)),
                stable_ai_percentage_point_threshold=float(config.get("single_stable_ai_percentage_point_threshold", 0.001)),
                significant_ai_percentage_point_threshold=float(config.get("single_significant_ai_percentage_point_threshold", 0.02)),
                significant_np_change=int(config.get("significant_np_change", 2)),
                progress_callback=progress_callback,
            )
            if progress_callback is not None:
                progress_callback({
                    "phase": "finalizing",
                    "status": "running",
                    "completed": len(selected_ids),
                    "total": len(selected_ids),
                    "evaluated_count": len(results),
                    "percent": 100.0,
                    "current_block_id": None,
                    "message": "正在汇总评价结果和地图数据",
                })
            grade_counts = {
                grade: sum(item["overall_grade"] == grade for item in results)
                for grade in (
                    "significantly_improved",
                    "slightly_improved",
                    "stable",
                    "slightly_worsened",
                    "significantly_worsened",
                )
            }
            improved = grade_counts["significantly_improved"] + grade_counts["slightly_improved"]
            worsened = grade_counts["significantly_worsened"] + grade_counts["slightly_worsened"]
            summary = {
                "evaluated_count": len(results),
                "improved_count": improved,
                "worsened_count": worsened,
                "balanced_count": grade_counts["stable"],
                "grade_counts": grade_counts,
                "average_demo_shape_score": round(
                    sum(float(item["demo_shape_score"]) for item in results) / len(results), 2
                ) if results else 0,
                "metric_directions": {
                    "NP": "下降为改善",
                    "PD": "下降为改善",
                    "LPI": "上升为改善",
                    "ED": "下降为改善",
                    "LSI": "下降为改善",
                    "AI": "上升为改善",
                },
                "methodology": {
                    "scope": "以完整现状城镇开发边界为调整前对象，将候选地块调入或调出后形成完整调整后边界并进行全域对比",
                    "administrative_boundary": "不参与计算；实际调入调出面积仍以原始城镇开发边界和街区几何为准",
                    "planning_geometry": f"六项指标统一采用规划评价几何：对不超过{adjacency_tolerance_m:g}米的相邻面逐对进行局部闭合验证，仅当相对边能够形成连续面时填补线性间隙；仅尖角接近不强制连接，辅助填补面积不计作实际调入面积",
                    "rasterization": "AI依据规划评价几何，采用统一坐标原点对齐的像元中心判定；NP、PD、LPI、ED、LSI依据同一规划评价几何计算",
                    "dimensions": {
                        "continuity": "连续完整性：NP、LPI",
                        "fragmentation": "破碎聚合性：PD、AI",
                        "regularity": "边界规整性：ED、LSI",
                    },
                    "topology_rules": {
                        "include": f"使用与六项指标相同的局部闭合验证，检查调入地块能否在{adjacency_tolerance_m:g}米内直接或经其他候选地块连接现状边界",
                        "exclude": f"使用与六项指标相同的规划评价几何，检查调出后是否造成原边界分裂",
                    },
                    "change_thresholds": {
                        "NP": "变化0为稳定、变化1为轻微、变化达到2为明显",
                        "PD_single": "单地块相对变化小于0.005%为稳定、0.005%至0.1%为轻微、达到0.1%为明显",
                        "PD_scenario": "组合方案相对变化小于0.05%为稳定、0.05%至0.5%为轻微、达到0.5%为明显",
                        "ED_LSI": "相对变化小于0.05%为稳定、0.05%至0.5%为轻微、达到0.5%为明显",
                        "LPI": "绝对变化小于0.01个百分点为稳定、0.01至0.1个百分点为轻微、达到0.1个百分点为明显",
                        "AI_single": "单地块绝对变化小于0.001个百分点为稳定、0.001至0.02个百分点为轻微、达到0.02个百分点为明显",
                        "AI_scenario": "组合方案绝对变化小于0.01个百分点为稳定、0.01至0.1个百分点为轻微、达到0.1个百分点为明显",
                        "dimension": "维度内指标得分取平均，绝对均值小于0.5为稳定、0.5至1.5为轻微、达到1.5为明显",
                        "topology": "拓扑风险独立报告，不再强制覆盖全域形态影响等级",
                    },
                    "NP": "统一规划评价几何中的有效独立斑块数",
                    "PD_unit": "个/100平方千米开发边界面积",
                    "LPI_unit": "%（最大规划连通组占完整开发边界面积）",
                    "ED_unit": "米/公顷开发边界面积",
                    "LSI": "0.25×边界长度/开发边界面积平方根",
                    "AI_unit": "%（同类四邻接数占紧凑布局最大邻接数）",
                },
                "note": f"每个候选地块均实际作用于完整现状城镇开发边界后独立评价；单地块PD采用0.005%/0.1%阈值，AI采用0.001/0.02个百分点阈值，NP、LPI、ED、LSI沿用既有规则；拓扑和六项指标统一使用经{adjacency_tolerance_m:g}米逐对局部闭合验证得到的规划评价几何。",
            }
            spatial = build_landscape_results_geojson(self.spatial_dataset.original, results)
            payload = {
                "type": "landscape_analysis",
                "boundary": self.landscape_boundary.summary(),
                "summary": summary,
                "results": results,
                "spatial": spatial,
            }
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            artifact = self._add_artifact(
                f"landscape_metrics_{timestamp}.json",
                "landscape",
                "application/json",
                preview=payload,
                content=content,
            )
            if progress_callback is not None:
                progress_callback({
                    "phase": "completed",
                    "status": "completed",
                    "completed": len(selected_ids),
                    "total": len(selected_ids),
                    "evaluated_count": len(results),
                    "percent": 100.0,
                    "current_block_id": None,
                    "message": f"已完成 {len(results)} 个地块的形态评价",
                })
            return {**payload, "artifact": artifact}

    def evaluate_landscape_scenario(
        self,
        block_ids: list[str],
        action: str,
        neighborhood_m: float | None,
        cell_size_m: float | None,
        connectivity: int | None,
    ) -> dict[str, Any]:
        """Evaluate one user-selected simultaneous inclusion or exclusion scenario."""

        with self._lock:
            if self.landscape_boundary is None:
                raise ValueError("请先上传现状城镇开发边界")
            if self.spatial_dataset is None:
                raise ValueError("请先上传包含空间几何的候选地块SHP ZIP")
            if action not in {"include", "exclude"}:
                raise ValueError("组合情景仅支持统一调入或统一调出")
            config = load_config().get("landscape", {})
            adjacency_tolerance_m = float(config.get("adjacency_tolerance_m", 300.0))
            scenario = evaluate_landscape_scenario(
                self.landscape_boundary,
                self.spatial_dataset.original,
                block_ids,
                action=action,
                neighborhood_m=float(neighborhood_m or config.get("neighborhood_m", 1500)),
                cell_size_m=float(cell_size_m or config.get("cell_size_m", 50)),
                connectivity=int(connectivity or config.get("connectivity", 8)),
                stable_relative_threshold=float(config.get("stable_relative_threshold", 0.0005)),
                significant_relative_threshold=float(config.get("significant_relative_threshold", 0.005)),
                stable_percentage_point_threshold=float(config.get("stable_percentage_point_threshold", 0.01)),
                significant_percentage_point_threshold=float(config.get("significant_percentage_point_threshold", 0.1)),
                significant_np_change=int(config.get("significant_np_change", 2)),
                adjacency_tolerance_m=adjacency_tolerance_m,
            )
            spatial = build_landscape_scenario_geojson(self.spatial_dataset.original, scenario)
            payload = {
                "type": "landscape_scenario_analysis",
                "boundary": self.landscape_boundary.summary(),
                "summary": {
                    "selected_count": scenario["selected_count"],
                    "action": scenario["action"],
                    "action_label": scenario["action_label"],
                    "group_count": len(scenario["groups"]),
                    "connection_counts": scenario["connection_counts"],
                    "overall_grade": scenario["overall_grade"],
                    "overall_label": scenario["overall_label"],
                    "note": f"组合情景先将所选地块整体作用于完整现状城镇开发边界，再对{adjacency_tolerance_m:g}米内的相邻面逐对执行局部闭合验证；六项指标和组合拓扑共同使用验证后的规划评价几何，并允许候选地块形成有效连接链。",
                },
                "scenario": scenario,
                "spatial": spatial,
            }
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            artifact = self._add_artifact(
                f"landscape_scenario_{timestamp}.json",
                "landscape",
                "application/json",
                preview=payload,
                content=content,
            )
            return {**payload, "artifact": artifact}

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
            if payload.get("decisions"):
                self.latest_analysis_payload = deepcopy(payload)
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

    def get_latest_analysis_payload(self) -> dict[str, Any] | None:
        """Return the latest decision-bearing analysis without exposing mutable workspace state."""

        with self._lock:
            return deepcopy(self.latest_analysis_payload)

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
                landscape_boundary=(
                    LandscapeBoundarySummary.model_validate(self.landscape_boundary.summary())
                    if self.landscape_boundary is not None
                    else LandscapeBoundarySummary()
                ),
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
