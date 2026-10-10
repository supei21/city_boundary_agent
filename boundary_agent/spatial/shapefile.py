from __future__ import annotations

import io
import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

from boundary_agent.gis import SHP_FIELD_ALIASES, TEXT_ATTRIBUTE_FIELDS
from boundary_agent.io import BOOL_FALSE, BOOL_TRUE, coerce


MAX_UPLOAD_BYTES = int(os.getenv("BOUNDARY_MAX_UPLOAD_MB", "1024")) * 1024 * 1024
MAX_EXTRACTED_BYTES = int(os.getenv("BOUNDARY_MAX_EXTRACTED_MB", "4096")) * 1024 * 1024
MAP_PREVIEW_FEATURES = int(os.getenv("BOUNDARY_MAP_MAX_FEATURES", "8000"))
MAP_SIMPLIFY_METERS = float(os.getenv("BOUNDARY_MAP_SIMPLIFY_METERS", "2"))
REQUIRED_COMPONENTS = {".shp", ".shx", ".dbf", ".prj"}
ADJUSTMENT_DECISIONS = {"一级优先调入", "二级优先调入", "建议调出", "内部更新", "人工复核"}
SHP_EXPORT_FIELDS = {
    "inside_boundary": "inside_bou",
    "poi_density": "poi_densit",
    "poi_industry": "poi_indust",
    "poi_commercial": "poi_commer",
    "poi_transport": "poi_transp",
    "poi_logistics": "poi_logist",
    "road_density": "road_densi",
    "pop_density": "pop_densit",
    "dominant_landuse": "dominant_l",
    "data_status": "data_statu",
    "main_road_density": "main_road_",
    "secondary_road_density": "secondary_",
    "branch_road_density": "branch_roa",
    "disaster_risk": "disaster_r",
    "ecological_redline": "ecological",
    "permanent_farmland": "permanent_",
    "prohibited_disaster_zone": "prohibited",
    "construction_ratio": "construc_",
    "previous_construction_ratio": "prev_build",
    "construction_change": "build_chg",
    "night_light_mean": "ntl_mean",
    "night_light_sum": "ntl_sum",
    "boundary_overlap_ratio": "udb_ratio",
    "farmland_ratio": "farm_ratio",
    "ecological_ratio": "eco_ratio",
    "industrial_ratio": "industrial",
    "residential_ratio": "residentia",
    "development_potential": "dev_score",
}

CONSTRAINT_LAYER_KINDS = {
    "urban_development_boundary": "urban_boundary",
    "ecological_redline": "ecological_redline",
    "farmland_permanent_basic_farmland": "permanent_farmland",
}


def _dbf_text(value: Any, max_bytes: int) -> str:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


@dataclass(slots=True)
class SpatialDataset:
    name: str
    temp_dir: tempfile.TemporaryDirectory[str]
    source_path: Path
    source_crs: str
    original: gpd.GeoDataFrame
    blocks: list[dict[str, Any]]
    warnings: list[str]

    def close(self) -> None:
        self.temp_dir.cleanup()


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    total_size = 0
    target_root = target.resolve()
    for member in archive.infolist():
        total_size += member.file_size
        if total_size > MAX_EXTRACTED_BYTES:
            limit_mb = MAX_EXTRACTED_BYTES // (1024 * 1024)
            raise ValueError(f"SHP压缩包解压后不能超过{limit_mb}MB")
        relative = Path(member.filename.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("SHP压缩包包含不安全的文件路径")
        destination = (target_root / relative).resolve()
        if target_root not in destination.parents and destination != target_root:
            raise ValueError("SHP压缩包包含越界文件路径")
    archive.extractall(target_root)


def _canonical_column(name: str) -> str:
    if name in SHP_FIELD_ALIASES:
        return SHP_FIELD_ALIASES[name]
    lowered = name.lower()
    lowered_aliases = {key.lower(): value for key, value in SHP_FIELD_ALIASES.items()}
    return lowered_aliases.get(lowered, name)


def _boolish(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in BOOL_TRUE or text in BOOL_TRUE:
            return True
        if text.lower() in BOOL_FALSE or text in BOOL_FALSE:
            return False
    return value


def _standardize_columns(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    renamed: dict[str, str] = {}
    occupied = set(frame.columns)
    for column in frame.columns:
        target = _canonical_column(str(column))
        if target != column and target not in occupied:
            renamed[str(column)] = target
            occupied.add(target)
    frame = frame.rename(columns=renamed).copy()
    if "block_id" not in frame.columns:
        seen: dict[str, int] = {}
        stable_ids: list[str] = []
        for _, row in frame.iterrows():
            district = str(row.get("block_name") or "")
            digest = hashlib.sha1(row.geometry.wkb + district.encode("utf-8")).hexdigest()[:12].upper()
            seen[digest] = seen.get(digest, 0) + 1
            suffix = f"_{seen[digest]:02d}" if seen[digest] > 1 else ""
            stable_ids.append(f"SHP_{digest}{suffix}")
        frame["block_id"] = stable_ids
    frame["block_id"] = frame["block_id"].astype(str)
    if "inside_boundary" not in frame.columns:
        frame["inside_boundary"] = False
    frame["inside_boundary"] = frame["inside_boundary"].map(_boolish).fillna(False).astype(bool)
    for field in (
        "construction_ratio",
        "previous_construction_ratio",
        "boundary_overlap_ratio",
        "farmland_ratio",
        "ecological_ratio",
    ):
        if field not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[field], errors="coerce")
        if numeric.notna().any() and float(numeric.max()) > 1 and float(numeric.max()) <= 100:
            numeric = numeric / 100
        frame[field] = numeric
    return frame


def _dataset_warnings(frame: gpd.GeoDataFrame, source_fields: set[str]) -> list[str]:
    warnings: list[str] = []
    if "inside_boundary" not in source_fields:
        warnings.append("未检测到现状开发边界标识，当前暂按边界外处理；调入/调出结论仅供演示。")

    constraint_fields = {
        "ecological_redline": "生态保护红线",
        "permanent_farmland": "永久基本农田",
        "prohibited_disaster_zone": "地质灾害禁止建设区",
    }
    missing_constraints = [label for field, label in constraint_fields.items() if field not in source_fields]
    if missing_constraints:
        warnings.append(f"缺少{'、'.join(missing_constraints)}字段，规划约束需要后续补充或叠加空间图层。")

    zero_fields: list[str] = []
    for field in (
        "poi_commercial",
        "poi_logistics",
        "pop_density",
        "boundary_overlap_ratio",
        "farmland_ratio",
        "ecological_ratio",
    ):
        if field not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[field], errors="coerce").dropna()
        if not numeric.empty and bool(numeric.eq(0).all()):
            zero_fields.append(field)
    if zero_fields:
        warnings.append(
            f"字段{'、'.join(zero_fields)}全部为 0，无法判断是确实无重叠/活动还是尚未赋值，"
            "相关结论需复核。"
        )

    ratio_fields = {"construction_ratio", "industrial_ratio", "residential_ratio"}
    if "dominant_landuse" in source_fields and not (ratio_fields & source_fields):
        warnings.append("主导用地为分类编码，但尚未配置编码含义，土地利用维度暂不计分。")
    return warnings


def _polygonal_geometry(geometry: Any) -> Polygon | MultiPolygon | None:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts = [
            part
            for item in geometry.geoms
            if (part := _polygonal_geometry(item)) is not None
        ]
        if not parts:
            return None
        merged = unary_union(parts)
        return merged if isinstance(merged, (Polygon, MultiPolygon)) else None
    return None


def _feature_collection(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    return json.loads(frame.to_json(drop_id=True, to_wgs84=False))


def _blocks_from_frame(frame: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    attributes = frame.drop(columns=frame.geometry.name)
    records = json.loads(attributes.to_json(orient="records", force_ascii=False))
    return [
        {
            key: str(value) if key in TEXT_ATTRIBUTE_FIELDS and value is not None else coerce(value)
            for key, value in record.items()
        }
        for record in records
    ]


def _constraint_kind(path: Path) -> str | None:
    stem = path.stem.lower().replace("-", "_").replace(" ", "_")
    if stem in CONSTRAINT_LAYER_KINDS:
        return CONSTRAINT_LAYER_KINDS[stem]
    if "development" in stem and "boundary" in stem:
        return "urban_boundary"
    if "ecological" in stem and "redline" in stem:
        return "ecological_redline"
    if "farmland" in stem and ("permanent" in stem or "basic" in stem):
        return "permanent_farmland"
    return None


def _overlap_ratio(geometry: gpd.GeoSeries, constraint_geometry: Any) -> pd.Series:
    denominator = geometry.area.replace(0, pd.NA)
    ratio = geometry.intersection(constraint_geometry).area / denominator
    return pd.to_numeric(ratio, errors="coerce").fillna(0.0).clip(0.0, 1.0)


def _polygonal_union(geometries: list[Any]) -> Polygon | MultiPolygon | None:
    merged = unary_union(geometries)
    polygonal = _polygonal_geometry(merged)
    if polygonal is not None:
        return polygonal
    polygons = list(polygonize(merged))
    if not polygons:
        return None
    polygonal = unary_union(polygons)
    return polygonal if isinstance(polygonal, (Polygon, MultiPolygon)) else None


def _has_existing_true(frame: gpd.GeoDataFrame, field: str) -> bool:
    if field not in frame.columns:
        return False
    return bool(frame[field].map(_boolish).fillna(False).astype(bool).any())


def apply_constraint_layers(dataset: SpatialDataset, source_path: Path) -> dict[str, int]:
    """Overlay uploaded three-line constraints onto the current parcel dataset."""
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    try:
        if source_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(source_path) as archive:
                _safe_extract(archive, root)
            spatial_paths = sorted(root.rglob("*.shp"))
        elif source_path.suffix.lower() in {".json", ".geojson"}:
            spatial_paths = [source_path]
        else:
            return {}

        grouped: dict[str, list[Any]] = {}
        for path in spatial_paths:
            kind = _constraint_kind(path)
            if kind is None:
                continue
            layer = gpd.read_file(path, engine="pyogrio")
            if layer.empty or layer.crs is None:
                continue
            if layer.crs != dataset.original.crs:
                layer = layer.to_crs(dataset.original.crs)
            layer = layer[layer.geometry.notna() & ~layer.geometry.is_empty].copy()
            if layer.empty:
                continue
            if (~layer.geometry.is_valid).any():
                layer.geometry = layer.geometry.make_valid()
            grouped.setdefault(kind, []).extend(layer.geometry.tolist())

        if not grouped:
            return {}

        frame = dataset.original.copy()
        applied: dict[str, int] = {}
        boundary_geometries = grouped.get("urban_boundary")
        if boundary_geometries:
            boundary = _polygonal_union(boundary_geometries)
            if boundary is not None:
                ratios = _overlap_ratio(frame.geometry, boundary)
                if bool(ratios.gt(0).any()) or not _has_existing_true(frame, "inside_boundary"):
                    frame["boundary_overlap_ratio"] = ratios
                    frame["inside_boundary"] = ratios.ge(0.5)
                    applied["urban_boundary"] = int(frame["inside_boundary"].sum())

        ecological_geometries = grouped.get("ecological_redline")
        if ecological_geometries:
            ecological = _polygonal_union(ecological_geometries)
            if ecological is not None:
                ratios = _overlap_ratio(frame.geometry, ecological)
                if bool(ratios.gt(0).any()) or not _has_existing_true(frame, "ecological_redline"):
                    frame["ecological_ratio"] = ratios
                    frame["ecological_redline"] = ratios.gt(0)
                    applied["ecological_redline"] = int(frame["ecological_redline"].sum())

        farmland_geometries = grouped.get("permanent_farmland")
        if farmland_geometries:
            farmland = _polygonal_union(farmland_geometries)
            if farmland is not None:
                ratios = _overlap_ratio(frame.geometry, farmland)
                if bool(ratios.gt(0).any()) or not _has_existing_true(frame, "permanent_farmland"):
                    frame["farmland_ratio"] = ratios
                    frame["permanent_farmland"] = ratios.gt(0)
                    applied["permanent_farmland"] = int(frame["permanent_farmland"].sum())

        dataset.original = frame
        dataset.blocks = _blocks_from_frame(frame)
        dataset.warnings = _dataset_warnings(frame, set(frame.columns))
        dataset.warnings.append("已将城镇开发边界、生态保护红线和永久基本农田图层叠加到候选地块。")
        return applied
    finally:
        temp_dir.cleanup()


def _read_shapefile_archive(name: str, archive_source: Any) -> SpatialDataset:
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    try:
        with zipfile.ZipFile(archive_source) as archive:
            _safe_extract(archive, root)
        shp_files = sorted(root.rglob("*.shp"))
        if not shp_files:
            raise ValueError("压缩包中未找到 .shp 文件")
        source_path = shp_files[0]
        present = {path.suffix.lower() for path in source_path.parent.glob(f"{source_path.stem}.*")}
        missing = REQUIRED_COMPONENTS - present
        if missing:
            missing_text = "、".join(sorted(missing))
            raise ValueError(f"SHP缺少必要组件：{missing_text}")

        frame = gpd.read_file(source_path, engine="pyogrio")
        if frame.empty:
            raise ValueError("SHP中没有地块要素")
        if frame.crs is None:
            raise ValueError("SHP缺少有效坐标系，请确认 .prj 文件")
        if not frame.geom_type.isin({"Polygon", "MultiPolygon"}).all():
            raise ValueError("当前Demo只接受Polygon或MultiPolygon地块图层")
        if (~frame.geometry.is_valid).any():
            frame.geometry = frame.geometry.make_valid()
        frame.geometry = frame.geometry.map(_polygonal_geometry)
        frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
        if frame.empty:
            raise ValueError("SHP中没有有效地块几何")

        source_fields = {_canonical_column(str(column)) for column in frame.columns}
        frame = _standardize_columns(frame)
        return SpatialDataset(
            name=name,
            temp_dir=temp_dir,
            source_path=source_path,
            source_crs=frame.crs.to_string(),
            original=frame,
            blocks=_blocks_from_frame(frame),
            warnings=_dataset_warnings(frame, source_fields),
        )
    except Exception:
        temp_dir.cleanup()
        raise


def read_shapefile_zip(name: str, content: bytes) -> SpatialDataset:
    """Read a Shapefile ZIP already held in memory (mainly for tests)."""

    if len(content) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"SHP压缩包不能超过{limit_mb}MB")
    return _read_shapefile_archive(name, io.BytesIO(content))


def read_shapefile_zip_path(name: str, archive_path: Path) -> SpatialDataset:
    """Read a Shapefile ZIP from disk without duplicating the archive in memory."""

    if archive_path.stat().st_size > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"SHP压缩包不能超过{limit_mb}MB")
    return _read_shapefile_archive(name, archive_path)


def build_analysis_geojson(
    dataset: SpatialDataset,
    payload: dict[str, Any] | None = None,
    max_features: int = MAP_PREVIEW_FEATURES,
) -> dict[str, Any]:
    payload = payload or {}
    selected_ids = {str(value) for value in payload.get("selected_block_ids", [])}
    decisions = {
        str(item.get("block_id")): item
        for item in payload.get("decisions", [])
        if item.get("block_id") is not None
    }
    priority_ids = selected_ids | set(decisions)
    ids = dataset.original["block_id"].astype(str)
    priority = dataset.original[ids.isin(priority_ids)].head(max_features)
    remaining = max(max_features - len(priority), 0)
    background = dataset.original[~ids.isin(priority_ids)].head(remaining)
    preview_frame = gpd.GeoDataFrame(
        pd.concat([priority, background], ignore_index=True),
        geometry=dataset.original.geometry.name,
        crs=dataset.original.crs,
    )
    web_preview = preview_frame.to_crs(epsg=3857)
    if MAP_SIMPLIFY_METERS > 0:
        web_preview.geometry = web_preview.geometry.simplify(
            MAP_SIMPLIFY_METERS,
            preserve_topology=True,
        )
    web_preview = web_preview.to_crs(epsg=4326)
    feature_collection = _feature_collection(web_preview)
    for feature in feature_collection["features"]:
        properties = feature.setdefault("properties", {})
        block_id = str(properties.get("block_id", ""))
        decision = decisions.get(block_id)
        properties["is_selected"] = block_id in selected_ids
        properties["is_candidate"] = bool(
            decision and decision.get("decision") in ADJUSTMENT_DECISIONS
        )
        if decision:
            properties.update(
                {
                    "ai_decision": decision.get("decision"),
                    "ai_suitability": decision.get("overall_suitability"),
                    "ai_development_probability": decision.get("development_probability"),
                    "ai_planning_match": decision.get("planning_match"),
                    "ai_constraint_cost": decision.get("constraint_cost"),
                    "ai_dynamic_score": decision.get("dynamic_score"),
                    "ai_confidence": decision.get("confidence"),
                    "ai_review": decision.get("manual_review_required"),
                    "ai_recommended_use": decision.get("recommended_use"),
                    "ai_key_drivers": "；".join(decision.get("key_drivers") or []),
                    "ai_constraint_status": decision.get("constraint_status"),
                    "ai_policy_compliance": decision.get("policy_compliance"),
                    "ai_policy_reason": decision.get("policy_reason"),
                    "ai_policy_requirements": "；".join(decision.get("policy_requirements") or []),
                    "ai_rank_city": decision.get("rank_city"),
                    "ai_rank_district": decision.get("rank_district"),
                    "ai_recommendation": decision.get("recommendation"),
                }
            )
    feature_collection["metadata"] = {
        "total_features": len(dataset.original),
        "displayed_features": len(preview_frame),
        "truncated": len(preview_frame) < len(dataset.original),
        "simplify_meters": MAP_SIMPLIFY_METERS,
    }
    return feature_collection


def export_candidate_shapefile(
    dataset: SpatialDataset,
    decisions: list[dict[str, Any]],
    output_zip: Path | None = None,
    decision_types: set[str] | None = None,
    output_name: str = "candidate_adjustment_blocks",
) -> tuple[str, bytes | Path] | None:
    allowed_decisions = ADJUSTMENT_DECISIONS if decision_types is None else decision_types
    decision_map = {
        str(item.get("block_id")): item
        for item in decisions
        if item.get("block_id") is not None
        and item.get("decision") in allowed_decisions
    }
    if not decision_map:
        return None
    subset = dataset.original[
        dataset.original["block_id"].astype(str).isin(decision_map)
    ].copy()
    if subset.empty:
        return None

    subset["AI_DEC"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("decision", "")
    )
    subset["AI_SCORE"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("overall_suitability")
    )
    subset["AI_DYN"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("dynamic_score")
    )
    subset["AI_PROB"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("development_probability")
    )
    subset["AI_PLAN"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("planning_match")
    )
    subset["AI_COST"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("constraint_cost")
    )
    subset["AI_CONF"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("confidence")
    )
    subset["AI_REVIEW"] = subset["block_id"].map(
        lambda value: "是" if decision_map[str(value)].get("manual_review_required") else "否"
    )
    subset["AI_RANK"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("rank_city")
    )
    subset["AI_DRANK"] = subset["block_id"].map(
        lambda value: decision_map[str(value)].get("rank_district")
    )
    subset["AI_USE"] = subset["block_id"].map(
        lambda value: _dbf_text(decision_map[str(value)].get("recommended_use"), 80)
    )
    subset["AI_DRIVER"] = subset["block_id"].map(
        lambda value: _dbf_text(
            "；".join(decision_map[str(value)].get("key_drivers") or []),
            250,
        )
    )
    subset["AI_NOTE"] = subset["block_id"].map(
        lambda value: _dbf_text(decision_map[str(value)].get("recommendation"), 250)
    )
    subset["AI_POLICY"] = subset["block_id"].map(
        lambda value: str(decision_map[str(value)].get("policy_compliance") or "未判读")[:20]
    )
    subset["AI_PREAS"] = subset["block_id"].map(
        lambda value: _dbf_text(decision_map[str(value)].get("policy_reason"), 250)
    )
    subset["AI_PREQ"] = subset["block_id"].map(
        lambda value: _dbf_text(
            "；".join(decision_map[str(value)].get("policy_requirements") or []),
            250,
        )
    )
    subset = subset.rename(
        columns={
            source: target
            for source, target in SHP_EXPORT_FIELDS.items()
            if source in subset.columns and target not in subset.columns
        }
    )

    with tempfile.TemporaryDirectory() as output_root:
        output_dir = Path(output_root)
        output_path = output_dir / f"{output_name}.shp"
        subset.to_file(
            output_path,
            driver="ESRI Shapefile",
            encoding="UTF-8",
            engine="pyogrio",
            index=False,
        )
        target: io.BytesIO | Path
        if output_zip is None:
            target = io.BytesIO()
        else:
            output_zip.parent.mkdir(parents=True, exist_ok=True)
            target = output_zip
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output_dir.glob(f"{output_name}.*")):
                archive.write(path, path.name)
        if isinstance(target, io.BytesIO):
            return f"{output_name}.shp.zip", target.getvalue()
        return target.name, target
