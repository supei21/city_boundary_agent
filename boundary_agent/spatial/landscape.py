"""Deterministic landscape-pattern evaluation for boundary adjustment scenarios."""

from __future__ import annotations

import math
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import geopandas as gpd
import numpy as np
from shapely import STRtree, contains_xy, make_valid, union_all
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping
from shapely.ops import polygonize_full

from boundary_agent.spatial.shapefile import (
    MAX_EXTRACTED_BYTES,
    MAX_UPLOAD_BYTES,
    REQUIRED_COMPONENTS,
    _constraint_kind,
)


LandscapeAction = Literal["include", "exclude", "auto"]
METRIC_DIRECTIONS = {
    "NP": -1,
    "PD": -1,
    "LPI": 1,
    "ED": -1,
    "LSI": -1,
    "AI": 1,
}

DIMENSION_METRICS = {
    "continuity": {
        "label": "连续完整性",
        "metrics": ("NP", "LPI"),
    },
    "fragmentation": {
        "label": "破碎聚合性",
        "metrics": ("PD", "AI"),
    },
    "regularity": {
        "label": "边界规整性",
        "metrics": ("ED", "LSI"),
    },
}

GRADE_LABELS = {
    "significantly_improved": "明显改善",
    "slightly_improved": "轻微改善",
    "stable": "基本稳定",
    "slightly_worsened": "轻微恶化",
    "significantly_worsened": "明显恶化",
}

PERCENTAGE_POINT_METRICS = {"LPI", "AI"}
TOPOLOGY_RISK_LABELS = {
    "none": "无拓扑风险",
    "low": "低拓扑风险",
    "medium": "中等拓扑风险",
    "high": "高拓扑风险",
}


@dataclass(slots=True)
class LandscapeBoundary:
    name: str
    temp_dir: tempfile.TemporaryDirectory[str]
    source_path: Path
    source_crs: str
    geometry: Polygon | MultiPolygon
    analysis_crs: str
    source_geometry_type: str
    source_feature_count: int
    patch_count: int
    area_km2: float
    warnings: list[str]

    def close(self) -> None:
        self.temp_dir.cleanup()

    def summary(self) -> dict[str, Any]:
        return {
            "loaded": True,
            "name": self.name,
            "source_crs": self.source_crs,
            "analysis_crs": self.analysis_crs,
            "source_geometry_type": self.source_geometry_type,
            "source_feature_count": self.source_feature_count,
            "patch_count": self.patch_count,
            "area_km2": self.area_km2,
            "warnings": list(self.warnings),
        }


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    target_root = target.resolve()
    total_size = 0
    for member in archive.infolist():
        total_size += member.file_size
        if total_size > MAX_EXTRACTED_BYTES:
            raise ValueError("开发边界压缩包解压后体积超过限制")
        relative = Path(member.filename.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("开发边界压缩包包含不安全路径")
        destination = (target_root / relative).resolve()
        if destination != target_root and target_root not in destination.parents:
            raise ValueError("开发边界压缩包包含越界路径")
    archive.extractall(target_root)


def _polygonal(geometry: Any) -> Polygon | MultiPolygon | None:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts = [part for item in geometry.geoms if (part := _polygonal(item)) is not None]
        if parts:
            merged = union_all(parts)
            return merged if isinstance(merged, (Polygon, MultiPolygon)) else None
    return None


def _patch_count(geometry: Polygon | MultiPolygon) -> int:
    return len(geometry.geoms) if isinstance(geometry, MultiPolygon) else 1


def _polygon_parts(geometry: Polygon | MultiPolygon | None) -> list[Polygon]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [part for part in geometry.geoms if isinstance(part, Polygon) and not part.is_empty]


def _planning_patch_areas(
    geometry: Polygon | MultiPolygon | None,
    tolerance_m: float,
) -> list[float]:
    """Group polygon parts separated only by a planning-scale linear gap."""

    parts = _polygon_parts(geometry)
    if not parts:
        return []
    adjacency: list[set[int]] = [set() for _ in parts]
    tree = STRtree(parts)
    for left, part in enumerate(parts):
        nearby = tree.query(part, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            right = int(value)
            if right <= left:
                continue
            adjacency[left].add(right)
            adjacency[right].add(left)

    areas: list[float] = []
    remaining = set(range(len(parts)))
    while remaining:
        start = remaining.pop()
        stack = [start]
        component_area = 0.0
        while stack:
            current = stack.pop()
            component_area += float(parts[current].area)
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        areas.append(component_area)
    return areas


def _planning_pair_link(
    left_geometry: Polygon | MultiPolygon,
    right_geometry: Polygon | MultiPolygon,
    tolerance_m: float,
) -> tuple[bool, Polygon | MultiPolygon | None, str]:
    """Validate one planning-scale connection and return its local fill geometry.

    A distance threshold alone also links polygons that merely approach at two
    corners.  Such a relationship must not become a filled development strip.
    We therefore test the pair locally: shared edges/overlap are accepted
    directly, while a non-zero gap is accepted only when a morphological close
    at half the configured tolerance really turns the pair into one polygon.
    """

    distance = float(left_geometry.distance(right_geometry))
    epsilon = max(0.001, tolerance_m * 1e-6)
    if distance > tolerance_m + epsilon:
        return False, None, "beyond_tolerance"

    pair = _polygonal(make_valid(union_all([left_geometry, right_geometry])))
    if pair is None or pair.is_empty:
        return False, None, "invalid"

    if distance <= epsilon:
        overlap_area = float(left_geometry.intersection(right_geometry).area)
        shared_edge = float(
            left_geometry.boundary.intersection(right_geometry.boundary).length
        )
        # A zero-area, zero-length point contact is treated as a corner
        # proximity rather than a structurally reliable connection.
        if overlap_area <= epsilon * epsilon and shared_edge <= epsilon:
            return False, None, "point_contact"
        return True, pair, "touching"

    radius = tolerance_m / 2 + epsilon
    closed = pair.buffer(radius, join_style="mitre").buffer(-radius, join_style="mitre")
    evaluated = _polygonal(make_valid(union_all([pair, closed])))
    if evaluated is None or evaluated.is_empty or _patch_count(evaluated) != 1:
        return False, None, "corner_proximity"
    return True, evaluated, "facing_gap"


def _build_planning_evaluation_geometry(
    geometry: Polygon | MultiPolygon | None,
    tolerance_m: float,
) -> tuple[Polygon | MultiPolygon | None, dict[str, int]]:
    """Build one geometry shared by topology checks and all six metrics.

    Accepted pair links are reduced to a minimum spanning forest.  This avoids
    repeatedly filling the same gap when several neighbouring polygons belong
    to one group, while still allowing boundary -> parcel -> parcel chains.
    """

    parts = _polygon_parts(geometry)
    diagnostics = {
        "source_patch_count": len(parts),
        "accepted_link_count": 0,
        "rejected_near_link_count": 0,
        "evaluation_patch_count": len(parts),
    }
    if not parts:
        return None, diagnostics
    raw = _polygonal(make_valid(union_all(parts)))
    if tolerance_m <= 0 or len(parts) == 1:
        diagnostics["evaluation_patch_count"] = _patch_count(raw) if raw is not None else 0
        return raw, diagnostics

    edges: list[tuple[float, int, int, Polygon | MultiPolygon]] = []
    tree = STRtree(parts)
    for left, part in enumerate(parts):
        nearby = tree.query(part, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            right = int(value)
            if right <= left:
                continue
            accepted, link_geometry, _ = _planning_pair_link(
                part,
                parts[right],
                tolerance_m,
            )
            if accepted and link_geometry is not None:
                edges.append((float(part.distance(parts[right])), left, right, link_geometry))
            else:
                diagnostics["rejected_near_link_count"] += 1

    parent = list(range(len(parts)))
    rank = [0] * len(parts)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def merge(left: int, right: int) -> bool:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return False
        if rank[left_root] < rank[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        if rank[left_root] == rank[right_root]:
            rank[left_root] += 1
        return True

    accepted_fills: list[Polygon | MultiPolygon] = []
    for _, left, right, link_geometry in sorted(edges, key=lambda item: item[:3]):
        if merge(left, right):
            accepted_fills.append(link_geometry)
            diagnostics["accepted_link_count"] += 1

    evaluated = _polygonal(make_valid(union_all([*parts, *accepted_fills])))
    diagnostics["evaluation_patch_count"] = _patch_count(evaluated) if evaluated is not None else 0
    return evaluated, diagnostics


def _planning_evaluation_geometry(
    geometry: Polygon | MultiPolygon | None,
    tolerance_m: float,
) -> Polygon | MultiPolygon | None:
    """Return the validated planning geometry without its diagnostics."""

    evaluated, _ = _build_planning_evaluation_geometry(geometry, tolerance_m)
    return evaluated


def _updated_planning_cells(
    before_geometry: Polygon | MultiPolygon | None,
    after_geometry: Polygon | MultiPolygon | None,
    before_cells: np.ndarray,
    cell_size_m: float,
) -> np.ndarray:
    """Update sparse raster cells from the planning-geometry difference only."""

    if before_geometry is None or before_geometry.is_empty:
        return _rasterized_class_cell_ids(after_geometry, cell_size_m)
    if after_geometry is None or after_geometry.is_empty:
        return np.empty(0, dtype=np.uint64)
    removed = _polygonal(make_valid(before_geometry.difference(after_geometry)))
    added = _polygonal(make_valid(after_geometry.difference(before_geometry)))
    removed_cells = _rasterized_class_cell_ids(removed, cell_size_m)
    added_cells = _rasterized_class_cell_ids(added, cell_size_m)
    retained = np.setdiff1d(before_cells, removed_cells, assume_unique=True)
    return np.union1d(retained, added_cells)


def _planning_patch_metrics(
    geometry: Polygon | MultiPolygon | None,
    bounds: tuple[float, float, float, float],
    tolerance_m: float,
) -> dict[str, float]:
    """Calculate NP/PD/LPI with the same planning tolerance as topology."""

    if geometry is None or geometry.is_empty:
        areas: list[float] = []
    else:
        clipped = _polygonal(make_valid(geometry.intersection(box(*bounds))))
        areas = _planning_patch_areas(clipped, tolerance_m)
    patch_count = len(areas)
    landscape_area_m2 = max(0.0, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
    largest_area_m2 = max(areas, default=0.0)
    return {
        "NP": float(patch_count),
        "PD": round(patch_count / (landscape_area_m2 / 100_000_000), 6) if landscape_area_m2 else 0.0,
        "LPI": round(largest_area_m2 / landscape_area_m2 * 100, 6) if landscape_area_m2 else 0.0,
    }

def _rasterized_class_cell_ids(
    geometry: Polygon | MultiPolygon | None,
    cell_size_m: float,
    *,
    max_query_cells: int = 250_000,
) -> np.ndarray:
    """Rasterize occupied envelopes on one CRS-aligned global grid.

    This avoids allocating a citywide background raster. The grid is anchored at
    the analysis CRS origin, so before and after always use the same alignment.
    """

    parts = _polygon_parts(geometry)
    if not parts:
        return np.empty(0, dtype=np.uint64)

    occupied_pairs: list[np.ndarray] = []
    for part in parts:
        min_x, min_y, max_x, max_y = part.bounds
        min_col = math.floor(min_x / cell_size_m)
        max_col = math.floor(max_x / cell_size_m)
        min_row = math.floor(min_y / cell_size_m)
        max_row = math.floor(max_y / cell_size_m)
        column_count = max_col - min_col + 1
        rows_per_chunk = max(1, max_query_cells // max(column_count, 1))
        columns = np.arange(min_col, max_col + 1, dtype=np.int64)
        x_values = (columns.astype(float) + 0.5) * cell_size_m
        for row_start in range(min_row, max_row + 1, rows_per_chunk):
            row_stop = min(max_row + 1, row_start + rows_per_chunk)
            rows = np.arange(row_start, row_stop, dtype=np.int64)
            y_values = (rows.astype(float) + 0.5) * cell_size_m
            grid_x, grid_y = np.meshgrid(x_values, y_values)
            inside = np.asarray(contains_xy(part, grid_x, grid_y), dtype=bool)
            inside_rows, inside_cols = np.nonzero(inside)
            if inside_rows.size:
                occupied_pairs.append(np.column_stack((
                    rows[inside_rows],
                    columns[inside_cols],
                )))

    if not occupied_pairs:
        return np.empty(0, dtype=np.uint64)
    pairs = np.unique(np.concatenate(occupied_pairs, axis=0), axis=0)
    offset = -np.iinfo(np.int32).min
    rows = (pairs[:, 0] + offset).astype(np.uint64)
    columns = (pairs[:, 1] + offset).astype(np.uint64)
    ids = (rows << np.uint64(32)) | columns
    return np.asarray(np.unique(ids), dtype=np.uint64)


def _aggregation_index(occupied_ids: np.ndarray) -> float:
    """Return AI for sparse cells encoded with absolute row/column indices."""

    class_cells = int(occupied_ids.size)
    if not class_cells:
        return 0.0
    columns = occupied_ids & np.uint64(0xFFFFFFFF)
    horizontal_sources = occupied_ids[columns < np.uint64(0xFFFFFFFF)]
    horizontal = int(np.count_nonzero(np.isin(
        horizontal_sources + np.uint64(1),
        occupied_ids,
        assume_unique=True,
    )))
    vertical = int(np.count_nonzero(np.isin(
        occupied_ids + np.uint64(1 << 32),
        occupied_ids,
        assume_unique=True,
    )))
    maximum_adjacencies = _maximum_like_adjacencies(class_cells)
    if not maximum_adjacencies:
        return 0.0
    return round((horizontal + vertical) / maximum_adjacencies * 100, 6)


def _full_boundary_metrics(
    geometry: Polygon | MultiPolygon | None,
    cell_size_m: float,
    adjacency_tolerance_m: float,
    occupied_cell_ids: np.ndarray | None = None,
) -> dict[str, float]:
    """Calculate all metrics from one complete planning-evaluation geometry.

    Administrative borders and candidate-centred local windows do not
    participate in the calculation.
    """

    if geometry is None or geometry.is_empty:
        return {metric: 0.0 for metric in METRIC_DIRECTIONS}

    areas = _planning_patch_areas(geometry, adjacency_tolerance_m)
    patch_count = len(areas)
    total_area_m2 = float(geometry.area)
    largest_area_m2 = max(areas, default=0.0)
    edge_length_m = float(geometry.boundary.length)

    occupied_ids = (
        _rasterized_class_cell_ids(geometry, cell_size_m)
        if occupied_cell_ids is None
        else occupied_cell_ids
    )

    return {
        "NP": float(patch_count),
        "PD": round(patch_count / (total_area_m2 / 100_000_000), 6) if total_area_m2 else 0.0,
        "LPI": round(largest_area_m2 / total_area_m2 * 100, 6) if total_area_m2 else 0.0,
        "ED": round(edge_length_m / (total_area_m2 / 10_000), 6) if total_area_m2 else 0.0,
        "LSI": round(0.25 * edge_length_m / math.sqrt(total_area_m2), 6) if total_area_m2 else 0.0,
        "AI": _aggregation_index(occupied_ids),
    }


def _changed_parts_connect_to_boundary(
    changed_area: Polygon | MultiPolygon | None,
    boundary_geometry: Polygon | MultiPolygon,
    tolerance_m: float,
) -> bool:
    """Return whether every changed part reaches the boundary through accepted links."""

    parts = _polygon_parts(changed_area)
    if not parts:
        return True
    adjacency: list[set[int]] = [set() for _ in parts]
    tree = STRtree(parts)
    for left, part in enumerate(parts):
        nearby = tree.query(part, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            right = int(value)
            if right <= left:
                continue
            accepted, _, _ = _planning_pair_link(part, parts[right], tolerance_m)
            if accepted:
                adjacency[left].add(right)
                adjacency[right].add(left)

    boundary_parts = _polygon_parts(boundary_geometry)
    boundary_tree = STRtree(boundary_parts)
    reachable: set[int] = set()
    for index, part in enumerate(parts):
        nearby = boundary_tree.query(part, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            accepted, _, _ = _planning_pair_link(
                part,
                boundary_parts[int(value)],
                tolerance_m,
            )
            if accepted:
                reachable.add(index)
                break
    stack = list(reachable)
    while stack:
        current = stack.pop()
        for neighbor in adjacency[current]:
            if neighbor not in reachable:
                reachable.add(neighbor)
                stack.append(neighbor)
    return len(reachable) == len(parts)


def read_landscape_boundary_zip_path(name: str, archive_path: Path) -> LandscapeBoundary:
    """Read a polygon or closed-line Shapefile ZIP as the baseline boundary."""

    if archive_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("开发边界压缩包超过上传限制")
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, root)
        shp_files = sorted(root.rglob("*.shp"))
        if not shp_files:
            raise ValueError("开发边界压缩包中未找到 .shp 文件")
        source_path = shp_files[0]
        present = {path.suffix.lower() for path in source_path.parent.glob(f"{source_path.stem}.*")}
        missing = REQUIRED_COMPONENTS - present
        if missing:
            raise ValueError(f"开发边界SHP缺少必要组件：{'、'.join(sorted(missing))}")
        frame = gpd.read_file(source_path, engine="pyogrio")
        if frame.empty or frame.crs is None:
            raise ValueError("开发边界为空或缺少有效坐标系")

        warnings: list[str] = []
        geometry_types = set(frame.geom_type)
        source_geometry_type = ",".join(sorted(geometry_types))
        if geometry_types <= {"LineString", "MultiLineString"}:
            polygons, cuts, dangles, invalid = polygonize_full(union_all(frame.geometry.values))
            if cuts.geoms or dangles.geoms or invalid.geoms:
                raise ValueError(
                    "开发边界线网未完全闭合："
                    f"切断线{len(cuts.geoms)}、悬挂线{len(dangles.geoms)}、无效环{len(invalid.geoms)}"
                )
            polygon_parts = list(polygons.geoms)
            if not polygon_parts:
                raise ValueError("开发边界线网无法重建为面")
            geometry = union_all(polygon_parts)
            warnings.append("输入为边界线，已依据闭合线网自动重建为面。")
        elif geometry_types <= {"Polygon", "MultiPolygon"}:
            geometry = union_all(frame.geometry.values)
        else:
            raise ValueError(f"开发边界仅支持线或面图层，当前类型：{source_geometry_type}")

        geometry = _polygonal(make_valid(geometry))
        if geometry is None or geometry.is_empty:
            raise ValueError("开发边界没有可用的面状几何")

        metric_crs = frame.crs
        if not metric_crs.is_projected or metric_crs.to_epsg() == 3857:
            metric_crs = frame.estimate_utm_crs()
        if metric_crs is None:
            raise ValueError("无法为开发边界确定米制分析坐标系")
        metric_geometry = gpd.GeoSeries([geometry], crs=frame.crs).to_crs(metric_crs).iloc[0]
        if frame.crs.to_epsg() == 3857:
            warnings.append("源数据为EPSG:3857，面积和长度计算已转换到局部米制投影。")
        return LandscapeBoundary(
            name=name,
            temp_dir=temp_dir,
            source_path=source_path,
            source_crs=frame.crs.to_string(),
            geometry=geometry,
            analysis_crs=metric_crs.to_string(),
            source_geometry_type=source_geometry_type,
            source_feature_count=len(frame),
            patch_count=_patch_count(geometry),
            area_km2=round(float(metric_geometry.area / 1_000_000), 3),
            warnings=warnings,
        )
    except Exception:
        temp_dir.cleanup()
        raise


def read_landscape_boundary_from_constraint_zip(
    name: str,
    archive_path: Path,
) -> LandscapeBoundary | None:
    """Extract the single urban-boundary layer from a multi-layer constraint ZIP."""

    if archive_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("约束数据压缩包超过上传限制")
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, root)
        candidates = [
            path for path in sorted(root.rglob("*.shp"))
            if _constraint_kind(path) == "urban_boundary" or "城镇开发边界" in path.stem
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            names = "、".join(path.name for path in candidates)
            raise ValueError(f"约束数据中识别到多个城镇开发边界图层：{names}")

        source_path = candidates[0]
        boundary_zip = root / "__urban_development_boundary__.zip"
        with zipfile.ZipFile(boundary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for component in source_path.parent.glob(f"{source_path.stem}.*"):
                archive.write(component, component.name)
        return read_landscape_boundary_zip_path(name, boundary_zip)
    finally:
        temp_dir.cleanup()


def build_landscape_boundary_geojson(
    boundary: LandscapeBoundary,
    simplify_m: float = 2.0,
) -> dict[str, Any]:
    """Build a browser-ready GeoJSON layer for the loaded baseline boundary."""

    geometry = gpd.GeoSeries(
        [boundary.geometry],
        crs=boundary.source_crs,
    ).to_crs(boundary.analysis_crs)
    if simplify_m > 0:
        geometry = geometry.simplify(simplify_m, preserve_topology=True)
    web_geometry = geometry.to_crs(epsg=4326).iloc[0]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "layer_type": "urban_development_boundary",
                    "boundary_name": boundary.name,
                    "patch_count": boundary.patch_count,
                    "area_km2": boundary.area_km2,
                    "source_crs": boundary.source_crs,
                },
                "geometry": mapping(web_geometry),
            }
        ],
        "metadata": {
            "layer_type": "urban_development_boundary",
            "patch_count": boundary.patch_count,
            "area_km2": boundary.area_km2,
            "simplify_meters": simplify_m,
        },
    }


def build_landscape_results_geojson(
    candidate_frame: gpd.GeoDataFrame,
    results: list[dict[str, Any]],
    simplify_m: float = 2.0,
) -> dict[str, Any]:
    """Build an exact browser layer for parcels included in one landscape evaluation."""

    if candidate_frame.crs is None:
        raise ValueError("候选地块缺少坐标系")
    result_by_id = {str(item["block_id"]): item for item in results}
    result_order = {block_id: position for position, block_id in enumerate(result_by_id)}
    ids = candidate_frame["block_id"].astype(str)
    selected = candidate_frame.loc[ids.isin(result_by_id)].copy()
    if selected.empty:
        return {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {"layer_type": "landscape_adjustment", "evaluated_count": 0},
        }
    selected["_landscape_block_id"] = selected["block_id"].astype(str)
    selected["_landscape_order"] = selected["_landscape_block_id"].map(result_order)
    selected = selected.sort_values("_landscape_order")

    metric_crs = selected.crs if selected.crs.is_projected else selected.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("无法确定候选地块的米制分析坐标系")
    web_frame = selected.to_crs(metric_crs)
    if simplify_m > 0:
        web_frame.geometry = web_frame.geometry.simplify(simplify_m, preserve_topology=True)
    web_frame = web_frame.to_crs(epsg=4326)

    features: list[dict[str, Any]] = []
    for _, row in web_frame.iterrows():
        block_id = str(row["_landscape_block_id"])
        result = result_by_id[block_id]
        topology = result.get("topology_check") or {}
        features.append({
            "type": "Feature",
            "properties": {
                "layer_type": "landscape_adjustment",
                "block_id": block_id,
                "block_name": result.get("block_name"),
                "landscape_action": result.get("action"),
                "action_label": result.get("action_label"),
                "overall_grade": result.get("overall_grade"),
                "overall_label": result.get("overall_label"),
                "topology_status": topology.get("status"),
                "topology_label": topology.get("label"),
            },
            "geometry": mapping(row.geometry),
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "layer_type": "landscape_adjustment",
            "evaluated_count": len(features),
            "simplify_meters": simplify_m,
        },
    }


def build_landscape_scenario_geojson(
    candidate_frame: gpd.GeoDataFrame,
    scenario: dict[str, Any],
    simplify_m: float = 2.0,
) -> dict[str, Any]:
    """Build a browser layer for every parcel participating in one scenario."""

    if candidate_frame.crs is None:
        raise ValueError("候选地块缺少坐标系")
    connections = {
        str(item["block_id"]): item
        for item in scenario.get("block_connections", [])
    }
    selected = candidate_frame.loc[
        candidate_frame["block_id"].astype(str).isin(connections)
    ].copy()
    if selected.empty:
        return {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {"layer_type": "landscape_scenario", "selected_count": 0},
        }
    metric_crs = selected.crs if selected.crs.is_projected else selected.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("无法确定候选地块的米制分析坐标系")
    selected = selected.to_crs(metric_crs)
    if simplify_m > 0:
        selected.geometry = selected.geometry.simplify(simplify_m, preserve_topology=True)
    selected = selected.to_crs(epsg=4326)
    features: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        block_id = str(row["block_id"])
        connection = connections[block_id]
        features.append({
            "type": "Feature",
            "properties": {
                "layer_type": "landscape_scenario",
                "block_id": block_id,
                "block_name": connection.get("block_name"),
                "scenario_action": scenario.get("action"),
                "scenario_action_label": scenario.get("action_label"),
                "connection_status": connection.get("status"),
                "connection_label": connection.get("label"),
                "group_id": connection.get("group_id"),
                "scenario_overall_grade": scenario.get("overall_grade"),
                "scenario_overall_label": scenario.get("overall_label"),
            },
            "geometry": mapping(row.geometry),
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "layer_type": "landscape_scenario",
            "selected_count": len(features),
            "action": scenario.get("action"),
        },
    }


def _connected_components(mask: np.ndarray, connectivity: int) -> list[int]:
    visited = np.zeros(mask.shape, dtype=bool)
    neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if connectivity == 8:
        neighbors += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    sizes: list[int] = []
    height, width = mask.shape
    for row, col in np.argwhere(mask):
        if visited[row, col]:
            continue
        visited[row, col] = True
        stack = [(int(row), int(col))]
        size = 0
        while stack:
            current_row, current_col = stack.pop()
            size += 1
            for delta_row, delta_col in neighbors:
                next_row = current_row + delta_row
                next_col = current_col + delta_col
                if (
                    0 <= next_row < height
                    and 0 <= next_col < width
                    and mask[next_row, next_col]
                    and not visited[next_row, next_col]
                ):
                    visited[next_row, next_col] = True
                    stack.append((next_row, next_col))
        sizes.append(size)
    return sizes


def _maximum_like_adjacencies(cell_count: int) -> int:
    if cell_count <= 1:
        return 0
    best = 0
    for width in range(1, int(math.sqrt(cell_count)) * 2 + 3):
        full_rows, remainder = divmod(cell_count, width)
        if full_rows == 0:
            continue
        adjacency = full_rows * max(width - 1, 0) + max(full_rows - 1, 0) * width
        if remainder:
            adjacency += max(remainder - 1, 0) + remainder
        best = max(best, adjacency)
    return best


def calculate_landscape_metrics(
    mask: np.ndarray,
    cell_size_m: float,
    connectivity: int = 8,
) -> dict[str, float]:
    """Calculate six class-level metrics from a binary boundary mask."""

    if mask.ndim != 2:
        raise ValueError("景观指数输入必须是二维栅格")
    landscape_area_m2 = float(mask.size) * cell_size_m * cell_size_m
    class_cells = int(mask.sum())
    class_area_m2 = class_cells * cell_size_m * cell_size_m
    sizes = _connected_components(mask, connectivity)
    patch_count = len(sizes)
    largest_cells = max(sizes, default=0)

    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    edge_count = int(np.count_nonzero(padded[:, 1:] != padded[:, :-1]))
    edge_count += int(np.count_nonzero(padded[1:, :] != padded[:-1, :]))
    edge_length_m = edge_count * cell_size_m
    like_adjacencies = int(np.count_nonzero(mask[:, 1:] & mask[:, :-1]))
    like_adjacencies += int(np.count_nonzero(mask[1:, :] & mask[:-1, :]))
    maximum_adjacencies = _maximum_like_adjacencies(class_cells)

    return {
        "NP": float(patch_count),
        "PD": round(patch_count / (landscape_area_m2 / 100_000_000), 6) if landscape_area_m2 else 0.0,
        "LPI": round(largest_cells / mask.size * 100, 6) if mask.size else 0.0,
        "ED": round(edge_length_m / (landscape_area_m2 / 10_000), 6) if landscape_area_m2 else 0.0,
        "LSI": round(0.25 * edge_length_m / math.sqrt(class_area_m2), 6) if class_area_m2 else 0.0,
        "AI": round(like_adjacencies / maximum_adjacencies * 100, 6) if maximum_adjacencies else 0.0,
    }


def _aligned_bounds(candidate: Polygon | MultiPolygon, radius_m: float, cell_size_m: float) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = candidate.bounds
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    half_size = max(radius_m, (max_x - min_x) / 2 + cell_size_m, (max_y - min_y) / 2 + cell_size_m)
    return (
        math.floor((center_x - half_size) / cell_size_m) * cell_size_m,
        math.floor((center_y - half_size) / cell_size_m) * cell_size_m,
        math.ceil((center_x + half_size) / cell_size_m) * cell_size_m,
        math.ceil((center_y + half_size) / cell_size_m) * cell_size_m,
    )


def _rasterize_centers(geometry: Polygon | MultiPolygon, bounds: tuple[float, float, float, float], cell_size_m: float) -> np.ndarray:
    min_x, min_y, max_x, max_y = bounds
    columns = max(1, int(round((max_x - min_x) / cell_size_m)))
    rows = max(1, int(round((max_y - min_y) / cell_size_m)))
    if rows * columns > 500_000:
        raise ValueError("局部评价栅格超过50万个像元，请增大像元尺寸或减小邻域范围")
    x_values = min_x + (np.arange(columns) + 0.5) * cell_size_m
    y_values = max_y - (np.arange(rows) + 0.5) * cell_size_m
    grid_x, grid_y = np.meshgrid(x_values, y_values)
    return np.asarray(contains_xy(geometry, grid_x, grid_y), dtype=bool)


def _metric_assessments(
    before: dict[str, float],
    after: dict[str, float],
    stable_relative_threshold: float,
    significant_relative_threshold: float,
    stable_percentage_point_threshold: float,
    significant_percentage_point_threshold: float,
    significant_np_change: int,
    stable_pd_relative_threshold: float | None = None,
    significant_pd_relative_threshold: float | None = None,
    stable_ai_percentage_point_threshold: float | None = None,
    significant_ai_percentage_point_threshold: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Orient metric changes so positive scores always mean morphological improvement."""

    assessments: dict[str, dict[str, Any]] = {}
    for metric, direction in METRIC_DIRECTIONS.items():
        delta = after[metric] - before[metric]
        oriented = direction * delta
        if metric == "NP":
            magnitude = abs(delta)
            if magnitude < 1:
                strength = "stable"
            elif magnitude >= significant_np_change:
                strength = "significant"
            else:
                strength = "slight"
            threshold_basis = "count"
        elif metric in PERCENTAGE_POINT_METRICS:
            magnitude = abs(delta)
            metric_stable_threshold = (
                stable_ai_percentage_point_threshold
                if metric == "AI" and stable_ai_percentage_point_threshold is not None
                else stable_percentage_point_threshold
            )
            metric_significant_threshold = (
                significant_ai_percentage_point_threshold
                if metric == "AI" and significant_ai_percentage_point_threshold is not None
                else significant_percentage_point_threshold
            )
            if magnitude < metric_stable_threshold:
                strength = "stable"
            elif magnitude >= metric_significant_threshold:
                strength = "significant"
            else:
                strength = "slight"
            threshold_basis = "percentage_point"
        else:
            scale = max(abs(before[metric]), 1.0)
            magnitude = abs(delta) / scale
            metric_stable_threshold = (
                stable_pd_relative_threshold
                if metric == "PD" and stable_pd_relative_threshold is not None
                else stable_relative_threshold
            )
            metric_significant_threshold = (
                significant_pd_relative_threshold
                if metric == "PD" and significant_pd_relative_threshold is not None
                else significant_relative_threshold
            )
            if magnitude < metric_stable_threshold:
                strength = "stable"
            elif magnitude >= metric_significant_threshold:
                strength = "significant"
            else:
                strength = "slight"
            threshold_basis = "relative"

        if strength == "stable" or abs(oriented) <= 1e-9:
            state = "unchanged"
            score = 0
        elif oriented > 0:
            state = "improved"
            score = 2 if strength == "significant" else 1
        else:
            state = "worsened"
            score = -2 if strength == "significant" else -1
        assessments[metric] = {
            "state": state,
            "strength": strength,
            "score": score,
            "threshold_basis": threshold_basis,
            "relative_change": (
                round(abs(delta) / max(abs(before[metric]), 1.0), 6)
                if metric != "NP"
                else None
            ),
            "absolute_change": round(abs(delta), 6),
        }
    return assessments


def _grade_from_score(score: float) -> str:
    if score >= 1.5:
        return "significantly_improved"
    if score >= 0.5:
        return "slightly_improved"
    if score <= -1.5:
        return "significantly_worsened"
    if score <= -0.5:
        return "slightly_worsened"
    return "stable"


def _dimension_assessments(
    metric_assessments: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    dimensions: dict[str, dict[str, Any]] = {}
    for key, definition in DIMENSION_METRICS.items():
        metrics = list(definition["metrics"])
        score = sum(float(metric_assessments[metric]["score"]) for metric in metrics) / len(metrics)
        grade = _grade_from_score(score)
        dimensions[key] = {
            "label": definition["label"],
            "metrics": metrics,
            "score": score,
            "grade": grade,
            "grade_label": GRADE_LABELS[grade],
        }
    return dimensions


def _topology_risk_fields(
    risk: bool,
    changed_area: Polygon | MultiPolygon | None,
    boundary_geometry: Polygon | MultiPolygon,
) -> dict[str, Any]:
    affected_area_m2 = float(changed_area.area) if risk and changed_area is not None else 0.0
    boundary_area_m2 = float(boundary_geometry.area)
    affected_share_percent = (
        affected_area_m2 / boundary_area_m2 * 100
        if boundary_area_m2
        else 0.0
    )
    if not risk:
        level = "none"
    elif affected_share_percent < 0.01:
        level = "low"
    elif affected_share_percent < 0.1:
        level = "medium"
    else:
        level = "high"
    return {
        "risk_level": level,
        "risk_label": TOPOLOGY_RISK_LABELS[level],
        "affected_area_km2": round(affected_area_m2 / 1_000_000, 6),
        "affected_share_percent": round(affected_share_percent, 6),
    }


def _landscape_expansion_index(
    action: Literal["include", "exclude"],
    changed_area: Polygon | MultiPolygon | None,
    boundary_geometry: Polygon | MultiPolygon,
    buffer_m: float,
) -> dict[str, Any] | None:
    """Classify an inclusion as infill, edge expansion or outlying growth."""

    if action != "include" or changed_area is None or changed_area.is_empty:
        return None
    ring = changed_area.buffer(buffer_m).difference(changed_area)
    occupied_area = float(ring.intersection(boundary_geometry).area)
    vacant_area = max(0.0, float(ring.area) - occupied_area)
    denominator = occupied_area + vacant_area
    value = occupied_area / denominator * 100 if denominator else 0.0
    if occupied_area <= 1e-9:
        pattern, label = "outlying", "孤立式扩张"
    elif value > 50:
        pattern, label = "infill", "填充式扩张"
    else:
        pattern, label = "edge_expansion", "边缘式扩张"
    return {
        "index": round(value, 6),
        "pattern": pattern,
        "label": label,
        "buffer_m": buffer_m,
        "occupied_buffer_area_m2": round(occupied_area, 3),
        "vacant_buffer_area_m2": round(vacant_area, 3),
    }


def _topology_check(
    action: Literal["include", "exclude"],
    boundary_geometry: Polygon | MultiPolygon,
    adjusted_geometry: Polygon | MultiPolygon | None,
    planning_before: Polygon | MultiPolygon | None,
    planning_after: Polygon | MultiPolygon | None,
    changed_area: Polygon | MultiPolygon | None,
    cell_size_m: float,
    adjacency_tolerance_m: float,
) -> dict[str, Any]:
    before_patch_count = len(_polygon_parts(planning_before))
    after_patch_count = len(_polygon_parts(planning_after))
    if changed_area is None or changed_area.is_empty:
        return {
            "status": "no_change",
            "label": "无实际变化",
            "risk": False,
            "before_patch_count": before_patch_count,
            "after_patch_count": after_patch_count,
            "shared_boundary_length_m": 0.0,
            "nearest_distance_m": 0.0,
            "adjacency_tolerance_m": adjacency_tolerance_m,
            "message": "候选地块未形成实际边界变化。",
            **_topology_risk_fields(False, changed_area, boundary_geometry),
        }

    if action == "include":
        shared_length = float(changed_area.boundary.intersection(boundary_geometry.boundary).length)
        nearest_distance = float(changed_area.distance(boundary_geometry))
        planning_connected = _changed_parts_connect_to_boundary(
            changed_area,
            boundary_geometry,
            adjacency_tolerance_m,
        )
        if not planning_connected:
            status = "island"
            label = "新增孤立斑块"
            risk = True
            message = (
                f"调入地块未能通过{adjacency_tolerance_m:g}米近邻关系连接现状开发边界，"
                "形成新的孤立开发边界。"
            )
        elif nearest_distance > 0:
            status = "nearby_connected"
            label = "规划近邻连接"
            risk = False
            message = (
                f"调入地块距现状开发边界最近{nearest_distance:.1f}米，"
                f"在{adjacency_tolerance_m:g}米规划连通阈值内。"
            )
        elif shared_length < cell_size_m:
            status = "weak_connection"
            label = "弱连接"
            risk = False
            message = "调入地块与现有边界的共边长度较短，连接关系建议复核。"
        else:
            status = "connected"
            label = "保持连接"
            risk = False
            message = "调入地块与现有开发边界相连，未新增孤立斑块。"
        return {
            "status": status,
            "label": label,
            "risk": risk,
            "before_patch_count": before_patch_count,
            "after_patch_count": after_patch_count,
            "shared_boundary_length_m": round(shared_length, 3),
            "nearest_distance_m": round(nearest_distance, 3),
            "adjacency_tolerance_m": adjacency_tolerance_m,
            "message": message,
            **_topology_risk_fields(risk, changed_area, boundary_geometry),
        }

    if after_patch_count > before_patch_count:
        status = "split"
        label = "主体分裂"
        risk = True
        message = "调出后独立斑块数量增加，原有开发边界发生空间分裂。"
    else:
        status = "intact"
        label = "未造成分裂"
        risk = False
        message = "调出后未增加独立斑块，现有开发边界未被切断。"
    return {
        "status": status,
        "label": label,
        "risk": risk,
        "before_patch_count": before_patch_count,
        "after_patch_count": after_patch_count,
        "shared_boundary_length_m": 0.0,
        "nearest_distance_m": 0.0,
        "adjacency_tolerance_m": adjacency_tolerance_m,
        "message": message,
        **_topology_risk_fields(risk, changed_area, boundary_geometry),
    }


def _overall_grade(
    dimensions: dict[str, dict[str, Any]],
    topology: dict[str, Any],
) -> tuple[str, float]:
    del topology  # Topology is reported independently and no longer overrides magnitude.
    dimension_score = (
        sum(float(item["score"]) for item in dimensions.values()) / len(dimensions)
        if dimensions
        else 0.0
    )
    return _grade_from_score(dimension_score), round(dimension_score, 6)


def _shape_score(metric_assessments: dict[str, dict[str, Any]], topology: dict[str, Any]) -> float:
    del topology  # Keep this legacy display score independent from topology risk.
    metric_score = sum(int(item["score"]) for item in metric_assessments.values())
    score = 50 + metric_score / (len(METRIC_DIRECTIONS) * 2) * 50
    return round(float(np.clip(score, 0, 100)), 2)


def evaluate_landscape_change(
    boundary: LandscapeBoundary,
    candidate_frame: gpd.GeoDataFrame,
    block_ids: list[str],
    block_actions: dict[str, Literal["include", "exclude"]] | None = None,
    default_action: LandscapeAction = "auto",
    neighborhood_m: float = 1500,
    cell_size_m: float = 50,
    connectivity: int = 8,
    adjacency_tolerance_m: float = 300.0,
    stable_relative_threshold: float = 0.0005,
    significant_relative_threshold: float = 0.005,
    stable_pd_relative_threshold: float = 0.00005,
    significant_pd_relative_threshold: float = 0.001,
    stable_percentage_point_threshold: float = 0.01,
    significant_percentage_point_threshold: float = 0.1,
    stable_ai_percentage_point_threshold: float = 0.001,
    significant_ai_percentage_point_threshold: float = 0.02,
    significant_np_change: int = 2,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Compare the complete baseline boundary with each adjusted boundary."""

    if candidate_frame.crs is None:
        raise ValueError("候选地块缺少坐标系")
    metric_crs = candidate_frame.crs if candidate_frame.crs.is_projected else candidate_frame.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("无法确定候选地块的米制分析坐标系")
    candidates = candidate_frame.to_crs(metric_crs).copy()
    if (~candidates.geometry.is_valid).any():
        candidates.geometry = candidates.geometry.make_valid()
    boundary_geometry = gpd.GeoSeries([boundary.geometry], crs=boundary.source_crs).to_crs(metric_crs).iloc[0]
    block_actions = block_actions or {}
    index = {str(value): position for position, value in enumerate(candidates["block_id"].astype(str))}
    results: list[dict[str, Any]] = []
    planning_before, planning_before_diagnostics = _build_planning_evaluation_geometry(
        boundary_geometry,
        adjacency_tolerance_m,
    )
    baseline_cells = _rasterized_class_cell_ids(planning_before, cell_size_m)
    before = _full_boundary_metrics(
        planning_before,
        cell_size_m,
        0.0,
        baseline_cells,
    )

    total_count = len(block_ids)

    def publish_progress(completed: int, block_id: str) -> None:
        if progress_callback is None:
            return
        progress_callback({
            "phase": "evaluating",
            "status": "running",
            "completed": completed,
            "total": total_count,
            "evaluated_count": len(results),
            "percent": round(completed / total_count * 100, 1) if total_count else 100.0,
            "current_block_id": str(block_id),
            "message": f"正在评价候选地块 {completed}/{total_count}",
        })

    for block_number, block_id in enumerate(block_ids, start=1):
        position = index.get(str(block_id))
        if position is None:
            publish_progress(block_number, str(block_id))
            continue
        row = candidates.iloc[position]
        candidate = _polygonal(make_valid(row.geometry))
        if candidate is None or candidate.is_empty:
            publish_progress(block_number, str(block_id))
            continue
        overlap_ratio = float(candidate.intersection(boundary_geometry).area / candidate.area) if candidate.area else 0.0
        action = block_actions.get(str(block_id), default_action)
        if action == "auto":
            action = "exclude" if overlap_ratio >= 0.5 else "include"
        changed_area = candidate.intersection(boundary_geometry) if action == "exclude" else candidate.difference(boundary_geometry)
        adjusted_raw = boundary_geometry.difference(changed_area) if action == "exclude" else boundary_geometry.union(changed_area)
        adjusted = _polygonal(make_valid(adjusted_raw))
        planning_after, planning_after_diagnostics = _build_planning_evaluation_geometry(
            adjusted,
            adjacency_tolerance_m,
        )
        adjusted_cells = _updated_planning_cells(
            planning_before,
            planning_after,
            baseline_cells,
            cell_size_m,
        )
        after = _full_boundary_metrics(
            planning_after,
            cell_size_m,
            0.0,
            adjusted_cells,
        )
        delta = {metric: round(after[metric] - before[metric], 6) for metric in METRIC_DIRECTIONS}
        metric_assessments = _metric_assessments(
            before,
            after,
            stable_relative_threshold,
            significant_relative_threshold,
            stable_percentage_point_threshold,
            significant_percentage_point_threshold,
            significant_np_change,
            stable_pd_relative_threshold,
            significant_pd_relative_threshold,
            stable_ai_percentage_point_threshold,
            significant_ai_percentage_point_threshold,
        )
        improved = [metric for metric, item in metric_assessments.items() if item["state"] == "improved"]
        worsened = [metric for metric, item in metric_assessments.items() if item["state"] == "worsened"]
        dimensions = _dimension_assessments(metric_assessments)
        topology = _topology_check(
            action,
            boundary_geometry,
            adjusted,
            planning_before,
            planning_after,
            _polygonal(changed_area),
            cell_size_m,
            adjacency_tolerance_m,
        )
        expansion = _landscape_expansion_index(
            action,
            _polygonal(changed_area),
            boundary_geometry,
            adjacency_tolerance_m,
        )
        overall_grade, overall_score = _overall_grade(dimensions, topology)
        overall_label = GRADE_LABELS[overall_grade]
        dimension_text = "、".join(
            f"{item['label']}{item['grade_label']}" for item in dimensions.values()
        )
        if changed_area.is_empty:
            conclusion = "候选地块没有形成实际边界变化，六项指标保持不变。"
        else:
            risk_text = topology["risk_label"]
            expansion_text = f"{expansion['label']}（LEI={expansion['index']:.2f}）" if expansion else ""
            conclusion = (
                f"全域形态影响为{overall_label}：{dimension_text}。"
                f"拓扑检查为{risk_text}：{topology['message']}"
                + (f" 调入关系为{expansion_text}。" if expansion_text else "")
            )
        results.append({
            "block_id": str(block_id),
            "block_name": (
                None
                if row.get("block_name") is None
                or (isinstance(row.get("block_name"), float) and math.isnan(row.get("block_name")))
                else str(row.get("block_name"))
            ),
            "action": action,
            "action_label": "调入" if action == "include" else "调出",
            "boundary_overlap_ratio": round(overlap_ratio, 6),
            "changed_area_km2": round(float(changed_area.area / 1_000_000), 6),
            "evaluation_scope": "full_boundary",
            "reference_boundary_area_km2": round(float(boundary_geometry.area / 1_000_000), 6),
            "planning_evaluation": {
                "method": "validated_local_gap_fill",
                "tolerance_m": adjacency_tolerance_m,
                "before_area_km2": round(float(planning_before.area / 1_000_000), 6) if planning_before is not None else 0.0,
                "after_area_km2": round(float(planning_after.area / 1_000_000), 6) if planning_after is not None else 0.0,
                "before": planning_before_diagnostics,
                "after": planning_after_diagnostics,
                "consistent_patch_count": (
                    planning_before_diagnostics["evaluation_patch_count"] == int(before["NP"])
                    and planning_after_diagnostics["evaluation_patch_count"] == int(after["NP"])
                ),
            },
            "cell_size_m": cell_size_m,
            "connectivity": connectivity,
            "adjacency_tolerance_m": adjacency_tolerance_m,
            "before": before,
            "after": after,
            "delta": delta,
            "improved_metrics": improved,
            "worsened_metrics": worsened,
            "metric_assessments": metric_assessments,
            "dimensions": dimensions,
            "topology_check": topology,
            "expansion_pattern": expansion,
            "overall_grade": overall_grade,
            "overall_label": overall_label,
            "overall_score": overall_score,
            "demo_shape_score": _shape_score(metric_assessments, topology),
            "conclusion": conclusion,
        })
        publish_progress(block_number, str(block_id))
    return results


def _scenario_connection_groups(
    selected: gpd.GeoDataFrame,
    boundary_geometry: Polygon | MultiPolygon,
    action: Literal["include", "exclude"],
    tolerance_m: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify parcels with the same validated links used by metric geometry."""

    records = list(selected.itertuples(index=False))
    geometries = [make_valid(record.geometry) for record in records]
    block_ids = [str(record.block_id) for record in records]
    names = [getattr(record, "block_name", None) for record in records]
    adjacency: list[set[int]] = [set() for _ in records]
    review_adjacency: list[set[int]] = [set() for _ in records]
    tree = STRtree(geometries)
    for left, geometry in enumerate(geometries):
        nearby = tree.query(geometry, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            right = int(value)
            if right <= left:
                continue
            accepted, _, reason = _planning_pair_link(
                geometry,
                geometries[right],
                tolerance_m,
            )
            if accepted:
                adjacency[left].add(right)
                adjacency[right].add(left)
            elif reason in {"point_contact", "corner_proximity"}:
                review_adjacency[left].add(right)
                review_adjacency[right].add(left)

    boundary_parts = _polygon_parts(boundary_geometry)
    boundary_tree = STRtree(boundary_parts)
    direct: set[int] = set()
    review_near_boundary: set[int] = set()
    for index, geometry in enumerate(geometries):
        nearby = boundary_tree.query(geometry, predicate="dwithin", distance=tolerance_m)
        for value in nearby:
            accepted, _, reason = _planning_pair_link(
                geometry,
                boundary_parts[int(value)],
                tolerance_m,
            )
            if accepted:
                direct.add(index)
                break
            if reason in {"point_contact", "corner_proximity"}:
                review_near_boundary.add(index)
    components: list[list[int]] = []
    remaining = set(range(len(records)))
    while remaining:
        start = min(remaining)
        stack = [start]
        component: list[int] = []
        remaining.remove(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    connections: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for group_number, component in enumerate(components, start=1):
        anchored = bool(set(component) & direct)
        group_id = f"G{group_number}"
        member_ids = [block_ids[index] for index in component]
        groups.append({
            "group_id": group_id,
            "block_ids": member_ids,
            "block_count": len(member_ids),
            "connected_to_boundary": anchored if action == "include" else True,
        })
        for index in component:
            if action == "exclude":
                status, label = "removal_member", "组合调出成员"
            elif index in direct:
                status, label = "direct", "直接连接"
            elif anchored:
                status, label = "conditional", "条件连接"
            else:
                status = "isolated"
                label = (
                    "近邻未连接（需复核）"
                    if index in review_near_boundary or review_adjacency[index]
                    else "仍然孤立"
                )
            name = names[index]
            connections.append({
                "block_id": block_ids[index],
                "block_name": None if name is None else str(name),
                "status": status,
                "label": label,
                "group_id": group_id,
                "connected_to_boundary": anchored if action == "include" else True,
                "neighbor_block_ids": [block_ids[value] for value in sorted(adjacency[index])],
                "review_required": bool(
                    action == "include"
                    and not anchored
                    and (index in review_near_boundary or review_adjacency[index])
                ),
                "nearby_unlinked_block_ids": [
                    block_ids[value] for value in sorted(review_adjacency[index])
                ],
            })
    return connections, groups


def evaluate_landscape_scenario(
    boundary: LandscapeBoundary,
    candidate_frame: gpd.GeoDataFrame,
    block_ids: list[str],
    action: Literal["include", "exclude"],
    neighborhood_m: float = 1500,
    cell_size_m: float = 50,
    connectivity: int = 8,
    stable_relative_threshold: float = 0.0005,
    significant_relative_threshold: float = 0.005,
    stable_percentage_point_threshold: float = 0.01,
    significant_percentage_point_threshold: float = 0.1,
    significant_np_change: int = 2,
    adjacency_tolerance_m: float = 300.0,
) -> dict[str, Any]:
    """Apply a multi-parcel scenario, then compare the complete boundaries."""

    if not block_ids:
        raise ValueError("请至少选择一个调入或调出地块")
    if candidate_frame.crs is None:
        raise ValueError("候选地块缺少坐标系")
    metric_crs = candidate_frame.crs if candidate_frame.crs.is_projected else candidate_frame.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("无法确定候选地块的米制分析坐标系")
    candidates = candidate_frame.to_crs(metric_crs).copy()
    candidates["block_id"] = candidates["block_id"].astype(str)
    requested = list(dict.fromkeys(str(value) for value in block_ids))
    selected = candidates.loc[candidates["block_id"].isin(requested)].copy()
    found = set(selected["block_id"])
    missing = [value for value in requested if value not in found]
    if missing:
        raise ValueError(f"未找到所选地块：{'、'.join(missing[:10])}")
    if (~selected.geometry.is_valid).any():
        selected.geometry = selected.geometry.make_valid()
    boundary_geometry = gpd.GeoSeries([boundary.geometry], crs=boundary.source_crs).to_crs(metric_crs).iloc[0]
    selected_union = _polygonal(make_valid(union_all(selected.geometry.values)))
    if selected_union is None or selected_union.is_empty:
        raise ValueError("所选地块没有可用的面状几何")

    changed_area = (
        selected_union.difference(boundary_geometry)
        if action == "include"
        else selected_union.intersection(boundary_geometry)
    )
    adjusted_raw = (
        boundary_geometry.union(changed_area)
        if action == "include"
        else boundary_geometry.difference(changed_area)
    )
    adjusted = _polygonal(make_valid(adjusted_raw))
    planning_before, planning_before_diagnostics = _build_planning_evaluation_geometry(
        boundary_geometry,
        adjacency_tolerance_m,
    )
    planning_after, planning_after_diagnostics = _build_planning_evaluation_geometry(
        adjusted,
        adjacency_tolerance_m,
    )
    baseline_cells = _rasterized_class_cell_ids(planning_before, cell_size_m)
    adjusted_cells = _updated_planning_cells(
        planning_before,
        planning_after,
        baseline_cells,
        cell_size_m,
    )
    before = _full_boundary_metrics(
        planning_before,
        cell_size_m,
        0.0,
        baseline_cells,
    )
    after = _full_boundary_metrics(
        planning_after,
        cell_size_m,
        0.0,
        adjusted_cells,
    )
    delta = {metric: round(after[metric] - before[metric], 6) for metric in METRIC_DIRECTIONS}
    metric_assessments = _metric_assessments(
        before,
        after,
        stable_relative_threshold,
        significant_relative_threshold,
        stable_percentage_point_threshold,
        significant_percentage_point_threshold,
        significant_np_change,
    )
    dimensions = _dimension_assessments(metric_assessments)
    topology = _topology_check(
        action,
        boundary_geometry,
        adjusted,
        planning_before,
        planning_after,
        _polygonal(changed_area),
        cell_size_m,
        adjacency_tolerance_m,
    )
    connections, groups = _scenario_connection_groups(
        selected,
        boundary_geometry,
        action,
        adjacency_tolerance_m,
    )
    if action == "include" and not changed_area.is_empty:
        disconnected_groups = [group for group in groups if not group["connected_to_boundary"]]
        if disconnected_groups:
            topology = {
                **topology,
                "status": "island",
                "label": "存在孤立组团",
                "risk": True,
                "message": (
                    f"组合调入后仍有 {len(disconnected_groups)} 个地块组团未能通过"
                    f"{adjacency_tolerance_m:g}米近邻链连接现状开发边界。"
                ),
            }
        else:
            topology = {
                **topology,
                "status": "connected",
                "label": "组合连通",
                "risk": False,
                "message": (
                    f"全部所选地块均可通过不超过{adjacency_tolerance_m:g}米的近邻链"
                    "连接现状开发边界。"
                ),
            }
        topology = {
            **topology,
            **_topology_risk_fields(bool(topology["risk"]), _polygonal(changed_area), boundary_geometry),
        }
    expansion = _landscape_expansion_index(
        action,
        _polygonal(changed_area),
        boundary_geometry,
        adjacency_tolerance_m,
    )
    overall_grade, overall_score = _overall_grade(dimensions, topology)
    overall_label = GRADE_LABELS[overall_grade]
    dimension_text = "、".join(
        f"{item['label']}{item['grade_label']}" for item in dimensions.values()
    )
    connection_counts = {
        status: sum(item["status"] == status for item in connections)
        for status in ("direct", "conditional", "isolated", "removal_member")
    }
    if changed_area.is_empty:
        conclusion = "所选地块没有形成实际边界变化，六项指标保持不变。"
    else:
        risk_text = topology["risk_label"]
        expansion_text = f"{expansion['label']}（LEI={expansion['index']:.2f}）" if expansion else ""
        conclusion = (
            f"组合全域形态影响为{overall_label}：{dimension_text}。"
            f"拓扑检查为{risk_text}：{topology['message']}"
            + (f" 调入关系为{expansion_text}。" if expansion_text else "")
        )
    return {
        "evaluation_mode": "scenario",
        "selected_block_ids": requested,
        "selected_count": len(requested),
        "action": action,
        "action_label": "整体调入" if action == "include" else "整体调出",
        "changed_area_km2": round(float(changed_area.area / 1_000_000), 6),
        "evaluation_scope": "full_boundary",
        "reference_boundary_area_km2": round(float(boundary_geometry.area / 1_000_000), 6),
        "planning_evaluation": {
            "method": "validated_local_gap_fill",
            "tolerance_m": adjacency_tolerance_m,
            "before_area_km2": round(float(planning_before.area / 1_000_000), 6) if planning_before is not None else 0.0,
            "after_area_km2": round(float(planning_after.area / 1_000_000), 6) if planning_after is not None else 0.0,
            "before": planning_before_diagnostics,
            "after": planning_after_diagnostics,
            "consistent_patch_count": (
                planning_before_diagnostics["evaluation_patch_count"] == int(before["NP"])
                and planning_after_diagnostics["evaluation_patch_count"] == int(after["NP"])
            ),
        },
        "cell_size_m": cell_size_m,
        "connectivity": connectivity,
        "adjacency_tolerance_m": adjacency_tolerance_m,
        "before": before,
        "after": after,
        "delta": delta,
        "improved_metrics": [key for key, value in metric_assessments.items() if value["state"] == "improved"],
        "worsened_metrics": [key for key, value in metric_assessments.items() if value["state"] == "worsened"],
        "metric_assessments": metric_assessments,
        "dimensions": dimensions,
        "topology_check": topology,
        "expansion_pattern": expansion,
        "overall_grade": overall_grade,
        "overall_label": overall_label,
        "overall_score": overall_score,
        "demo_shape_score": _shape_score(metric_assessments, topology),
        "block_connections": connections,
        "connection_counts": connection_counts,
        "groups": groups,
        "conclusion": conclusion,
    }
