from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from boundary_agent.io import BOOL_FALSE, BOOL_TRUE, coerce


SHP_FIELD_ALIASES = {
    "id": "block_id",
    "ID": "block_id",
    "name": "block_name",
    "inside": "inside_boundary",
    "in_bound": "inside_boundary",
    "in_boundary": "inside_boundary",
    "inside_bou": "inside_boundary",
    "boundary": "inside_boundary",
    "poi_indust": "poi_industry",
    "poi_densit": "poi_density",
    "pop_densit": "pop_density",
    "dominant_l": "dominant_landuse",
    "poi_commer": "poi_commercial",
    "poi_transp": "poi_transport",
    "poi_logist": "poi_logistics",
    "road_densi": "road_density",
    "main_road_": "main_road_density",
    "secondary_": "secondary_road_density",
    "branch_roa": "branch_road_density",
    "disaster_r": "disaster_risk",
    "ecological": "ecological_redline",
    "eco_red": "ecological_redline",
    "permanent_": "permanent_farmland",
    "farmland": "permanent_farmland",
    "prohibited": "prohibited_disaster_zone",
    "pro_dis": "prohibited_disaster_zone",
    "construc_": "construction_ratio",
    "Bulit_prop": "previous_construction_ratio",
    "Bulit_pro2": "construction_ratio",
    "Bulit_CHAG": "construction_change",
    "NTL_MEAN": "night_light_mean",
    "NTL_SUM": "night_light_sum",
    "UDB_Ratio": "boundary_overlap_ratio",
    "Farm_Ratio": "farmland_ratio",
    "Eco_Ratio": "ecological_ratio",
    "data_statu": "data_status",
    "industrial": "industrial_ratio",
    "residentia": "residential_ratio",
    "dev_score": "development_potential",
    "DEV_SCORE": "development_potential",
}
TEXT_ATTRIBUTE_FIELDS = {
    "block_id",
    "block_name",
    "dominant_landuse",
    "disaster_risk",
    "data_status",
}


def _load_pyshp():
    try:
        import shapefile  # type: ignore
    except ImportError as exc:
        raise ImportError("处理SHP需要安装 pyshp：pip install pyshp") from exc
    return shapefile


def _boolish(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        lowered = stripped.lower()
        if lowered in BOOL_TRUE or stripped in BOOL_TRUE:
            return True
        if lowered in BOOL_FALSE or stripped in BOOL_FALSE:
            return False
    return value


def _canonicalize_shp_row(row: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(row)
    for source, target in SHP_FIELD_ALIASES.items():
        if source in row and target not in canonical:
            canonical[target] = row[source]
    return canonical


def _prepare_shapefile_workspace(files: dict[str, bytes]) -> tuple[tempfile.TemporaryDirectory, Path]:
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    zipped = next((name for name in files if name.lower().endswith(".zip")), None)
    if zipped:
        with zipfile.ZipFile(io.BytesIO(files[zipped])) as archive:
            archive.extractall(root)
    else:
        for name, content in files.items():
            (root / Path(name).name).write_bytes(content)

    shp_files = sorted(root.rglob("*.shp"))
    if not shp_files:
        temp_dir.cleanup()
        raise ValueError("未找到 .shp 文件。请上传SHP压缩包，或同时上传 .shp/.shx/.dbf。")
    return temp_dir, shp_files[0]


def read_shapefile_blocks(files: dict[str, bytes]) -> tuple[list[dict], dict[str, Any]]:
    """读取SHP属性表，并保留原始几何上下文用于分析结果回写。"""
    shapefile = _load_pyshp()
    temp_dir, shp_path = _prepare_shapefile_workspace(files)
    try:
        reader = shapefile.Reader(str(shp_path), encoding="utf-8")
    except UnicodeDecodeError:
        reader = shapefile.Reader(str(shp_path), encoding="gbk")

    field_names = [field[0] for field in reader.fields[1:]]
    blocks = []
    for index, record in enumerate(reader.records(), start=1):
        raw_row = _canonicalize_shp_row(dict(zip(field_names, record)))
        row = {
            field: str(value) if field in TEXT_ATTRIBUTE_FIELDS and value is not None else _boolish(coerce(value))
            for field, value in raw_row.items()
        }
        row.setdefault("block_id", row.get("id") or row.get("ID") or f"SHP_{index:04d}")
        if "inside_boundary" not in row:
            row["inside_boundary"] = row.get("inside") or row.get("in_boundary") or row.get("boundary") or False
        row["inside_boundary"] = bool(_boolish(row["inside_boundary"]))
        blocks.append(row)

    context = {
        "temp_dir": temp_dir,
        "shp_path": shp_path,
        "shape_type": reader.shapeType,
        "fields": reader.fields[1:],
    }
    reader.close()
    return blocks, context


def write_analyzed_shapefile(context: dict[str, Any], decisions: list[dict], output_zip: Path) -> Path:
    """把智能体分析结果作为AI_*字段写回SHP，并打包输出。"""
    shapefile = _load_pyshp()
    shp_path = Path(context["shp_path"])
    try:
        reader = shapefile.Reader(str(shp_path), encoding="utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        reader = shapefile.Reader(str(shp_path), encoding="gbk")
        encoding = "gbk"

    output_dir = output_zip.with_suffix("")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_base = output_dir / output_zip.with_suffix("").name
    writer = shapefile.Writer(str(output_base), shapeType=reader.shapeType, encoding=encoding)
    for field in reader.fields[1:]:
        writer.field(*field)
    writer.field("AI_DEC", "C", size=40)
    writer.field("AI_SCORE", "F", size=12, decimal=2)
    writer.field("AI_CONF", "F", size=8, decimal=3)
    writer.field("AI_REVIEW", "C", size=10)
    writer.field("AI_RULE", "C", size=24)
    writer.field("AI_NOTE", "C", size=254)

    decision_map = {str(item["block_id"]): item for item in decisions}
    field_names = [field[0] for field in reader.fields[1:]]
    for shape_record in reader.iterShapeRecords():
        attrs = _canonicalize_shp_row(dict(zip(field_names, shape_record.record)))
        decision = decision_map.get(str(attrs.get("block_id", "")), {})
        writer.shape(shape_record.shape)
        writer.record(
            *list(shape_record.record),
            decision.get("decision", ""),
            decision.get("overall_suitability", 0),
            decision.get("confidence", 0),
            "是" if decision.get("manual_review_required") else "否",
            decision.get("rule_version", ""),
            decision.get("ai_explanation") or decision.get("recommendation", ""),
        )
    writer.close()

    for suffix in (".prj", ".cpg"):
        source = shp_path.with_suffix(suffix)
        if source.exists():
            shutil.copy2(source, output_base.with_suffix(suffix))

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in output_dir.iterdir():
            archive.write(path, path.name)
    reader.close()
    return output_zip


def shapefile_to_geojson(context: dict[str, Any], max_features: int = 200) -> dict[str, Any]:
    shapefile = _load_pyshp()
    try:
        reader = shapefile.Reader(str(context["shp_path"]), encoding="utf-8")
    except UnicodeDecodeError:
        reader = shapefile.Reader(str(context["shp_path"]), encoding="gbk")

    field_names = [field[0] for field in reader.fields[1:]]
    features = []
    for shape_record in reader.iterShapeRecords():
        features.append(
            {
                "type": "Feature",
                "properties": dict(zip(field_names, shape_record.record)),
                "geometry": shape_record.shape.__geo_interface__,
            }
        )
        if len(features) >= max_features:
            break
    reader.close()
    return {"type": "FeatureCollection", "features": features}
