from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from boundary_agent.gis import SHP_FIELD_ALIASES


SCHEMA_VERSION = 2

# Only variables that are present in the current parcel database and can be
# reconstructed when a parcel dataset is uploaded are listed here.  Coordinates
# are deliberately excluded: they are used only to calculate neighbourhood
# statistics and are never passed to XGBoost directly.
BASE_NUMERIC_FEATURES = [
    "area_km2",
    "perimeter_",
    "poi_total",
    "poi_densit",
    "poi_indust",
    "poi_commer",
    "poi_public",
    "poi_transp",
    "poi_logist",
    "dem_mean",
    "dem_std",
    "dem_relief",
    "pop_raw_su",
    "Pop_densit",
    "road_lengt",
    "road_densi",
    "main_road_",
    "secondary_",
    "branch_roa",
    "NTL_MEAN",
    "NTL_MAX",
    "Water_prop",
    "Tree_prop",
    "Flood_prop",
    "Crop_prop",
    "Bulit_prop",
    "Bare_prop",
    "Sonw_prop",
    "Range_prop",
]

NEIGHBOURHOOD_SOURCES = [
    "poi_total",
    "poi_indust",
    "poi_commer",
    "poi_public",
    "poi_transp",
    "poi_logist",
]

FEATURE_LABELS = {
    "area_km2": "地块面积",
    "perimeter_": "地块周长",
    "poi_total": "地块POI总量",
    "poi_densit": "地块POI密度",
    "poi_indust": "产业POI数量",
    "poi_commer": "商业POI数量",
    "poi_public": "公共服务POI数量",
    "poi_transp": "交通POI数量",
    "poi_logist": "物流POI数量",
    "dem_mean": "平均高程",
    "dem_std": "高程标准差",
    "dem_relief": "地形起伏度",
    "pop_raw_su": "地块人口总量",
    "Pop_densit": "人口密度",
    "road_lengt": "道路总长度",
    "road_densi": "道路密度",
    "main_road_": "主干路密度",
    "secondary_": "次干路密度",
    "branch_roa": "支路密度",
    "NTL_MEAN": "平均夜间灯光",
    "NTL_MAX": "最大夜间灯光",
    "Water_prop": "水域比例",
    "Tree_prop": "林地比例",
    "Flood_prop": "淹水植被比例",
    "Crop_prop": "耕地比例",
    "Bulit_prop": "建成区比例",
    "Bare_prop": "裸地比例",
    "Sonw_prop": "冰雪比例",
    "Range_prop": "草地比例",
}


def make_feature_schema(dominant_categories: list[str]) -> dict[str, Any]:
    categories = [str(value).strip() for value in dominant_categories if str(value).strip()]
    schema: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "base_numeric_features": list(BASE_NUMERIC_FEATURES),
        "categorical_features": [
            {
                "source": "dominant_l",
                "categories": categories,
                "other": "OTHER",
            }
        ],
        "neighbourhood_features": [
            {
                "source": source,
                "coordinate_x": "centroid_x",
                "coordinate_y": "centroid_y",
                "radii_m": [500, 1000],
                "unit": "count_per_square_kilometre",
                "include_self": True,
            }
            for source in NEIGHBOURHOOD_SOURCES
        ],
        "directly_excluded": {
            "duplicate_geometry": ["Shape_Area", "Shape_Leng"],
            "location_memory": ["centroid_x", "centroid_y"],
            "future_or_label_leakage": [
                "Water_pro2", "Tree_pro2", "Flood_pro2", "Crop_pro2",
                "Bulit_pro2", "Bare_pro2", "Sonw_pro2", "Range_pro2",
                "Water_CHAG", "Tree_CHAG", "Flood_CHAG", "Crop_CHAG",
                "Bulit_CHAG", "Bare_CHAG", "Snow_CHAG", "Range_CHAG",
                "label_inclusion",
            ],
            "redundant_raw_landcover": [
                "VALUE_1", "VALUE_2", "VALUE_4", "VALUE_5", "VALUE_7",
                "VALUE_8", "VALUE_10", "VALUE_11", "Water", "Trees",
                "Flooded_Ve", "Crops", "Bulit_Area", "Bare_Groun",
                "Snow", "Rangeland",
            ],
            "project_features": [],
        },
        "requested_but_unavailable": [
            "population_growth",
            "distance_to_main_road_or_expressway_entrance",
            "distance_to_city_centre",
            "distance_to_industrial_park",
            "distance_to_rail_station",
        ],
        "feature_labels": dict(FEATURE_LABELS),
    }
    schema["feature_names"] = feature_names_from_schema(schema)
    for item in schema["neighbourhood_features"]:
        source = item["source"]
        base_label = FEATURE_LABELS.get(source, source)
        for radius in item["radii_m"]:
            name = neighbourhood_feature_name(source, int(radius))
            schema["feature_labels"][name] = f"{radius}米邻域{base_label}密度"
    for category in categories + ["OTHER"]:
        name = categorical_feature_name("dominant_l", category)
        schema["feature_labels"][name] = f"主导土地利用={category}"
    return schema


def feature_names_from_schema(schema: dict[str, Any]) -> list[str]:
    names = list(schema.get("base_numeric_features", []))
    for item in schema.get("neighbourhood_features", []):
        for radius in item.get("radii_m", []):
            names.append(neighbourhood_feature_name(item["source"], int(radius)))
    for item in schema.get("categorical_features", []):
        categories = list(item.get("categories", [])) + [item.get("other", "OTHER")]
        names.extend(categorical_feature_name(item["source"], value) for value in categories)
    return names


def neighbourhood_feature_name(source: str, radius_m: int) -> str:
    return f"{source}__nbr_density_{radius_m}m"


def categorical_feature_name(source: str, category: Any) -> str:
    safe = str(category).strip().replace(" ", "_") or "OTHER"
    return f"{source}__{safe}"


def load_feature_schema(model_dir: Path) -> dict[str, Any] | None:
    path = Path(model_dir) / "feature_schema.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_model_feature_frame(
    records: list[dict[str, Any]],
    feature_names: list[str],
    schema: dict[str, Any] | None,
) -> tuple[pd.DataFrame, list[str]]:
    if schema is None:
        return _build_legacy_frame(records, feature_names)

    rows: list[dict[str, Any]] = []
    missing_features: set[str] = set()
    for record in records:
        lower_map = {str(key).lower(): key for key in record}
        row: dict[str, Any] = {}
        for feature in schema.get("base_numeric_features", []):
            value = lookup_feature(record, lower_map, feature)
            if value is None:
                missing_features.add(feature)
            row[feature] = value
        for item in schema.get("categorical_features", []):
            source = item["source"]
            raw = lookup_feature(record, lower_map, source)
            category = str(raw).strip() if raw is not None else ""
            known = {str(value) for value in item.get("categories", [])}
            encoded = category if category in known else str(item.get("other", "OTHER"))
            for value in list(item.get("categories", [])) + [item.get("other", "OTHER")]:
                row[categorical_feature_name(source, value)] = float(str(value) == encoded)
        rows.append(row)

    frame = pd.DataFrame(rows)
    _add_neighbourhood_features(frame, records, schema, missing_features)
    for feature in feature_names:
        if feature not in frame.columns:
            frame[feature] = np.nan
            missing_features.add(feature)
    frame = frame[feature_names].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame, sorted(missing_features)


def _add_neighbourhood_features(
    frame: pd.DataFrame,
    records: list[dict[str, Any]],
    schema: dict[str, Any],
    missing_features: set[str],
) -> None:
    if not records:
        return
    first = schema.get("neighbourhood_features", [{}])[0]
    x_field = first.get("coordinate_x", "centroid_x")
    y_field = first.get("coordinate_y", "centroid_y")
    xs = _numeric_record_values(records, x_field)
    ys = _numeric_record_values(records, y_field)
    valid = np.isfinite(xs) & np.isfinite(ys)

    all_outputs = [
        neighbourhood_feature_name(item["source"], int(radius))
        for item in schema.get("neighbourhood_features", [])
        for radius in item.get("radii_m", [])
    ]
    if not valid.any():
        for name in all_outputs:
            frame[name] = np.nan
            missing_features.add(name)
        return

    valid_positions = np.flatnonzero(valid)
    tree = BallTree(np.column_stack([xs[valid], ys[valid]]), metric="euclidean")
    for item in schema.get("neighbourhood_features", []):
        source = item["source"]
        values = _numeric_record_values(records, source)
        values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
        for radius in item.get("radii_m", []):
            radius = int(radius)
            output = neighbourhood_feature_name(source, radius)
            result = np.full(len(records), np.nan, dtype=float)
            neighbours = tree.query_radius(
                np.column_stack([xs[valid], ys[valid]]),
                r=float(radius),
                return_distance=False,
            )
            area_km2 = math.pi * (radius / 1000.0) ** 2
            for local_index, neighbour_local_indexes in enumerate(neighbours):
                source_indexes = valid_positions[neighbour_local_indexes]
                result[valid_positions[local_index]] = float(values[source_indexes].sum()) / area_km2
            frame[output] = result
            if not np.isfinite(result).any():
                missing_features.add(output)


def _numeric_record_values(records: list[dict[str, Any]], feature: str) -> np.ndarray:
    values = []
    for record in records:
        lower_map = {str(key).lower(): key for key in record}
        values.append(lookup_feature(record, lower_map, feature))
    return pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)


def lookup_feature(record: dict[str, Any], lower_map: dict[str, str], feature: str) -> Any:
    if feature in record:
        return record[feature]
    direct = lower_map.get(feature.lower())
    if direct is not None:
        return record[direct]
    aliases = {str(key).lower(): value for key, value in SHP_FIELD_ALIASES.items()}
    canonical = SHP_FIELD_ALIASES.get(feature) or aliases.get(feature.lower())
    if canonical in record:
        return record[canonical]
    canonical_key = lower_map.get(str(canonical).lower()) if canonical else None
    return record[canonical_key] if canonical_key is not None else None


def _build_legacy_frame(
    records: list[dict[str, Any]], feature_names: list[str]
) -> tuple[pd.DataFrame, list[str]]:
    rows = []
    missing_features: set[str] = set()
    for record in records:
        lower_map = {str(key).lower(): key for key in record}
        row = {}
        for feature in feature_names:
            value = lookup_feature(record, lower_map, feature)
            if value is None:
                missing_features.add(feature)
            row[feature] = value
        rows.append(row)
    frame = pd.DataFrame(rows, columns=feature_names).apply(pd.to_numeric, errors="coerce")
    return frame.replace([np.inf, -np.inf], np.nan), sorted(missing_features)
