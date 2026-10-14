from __future__ import annotations

from typing import Any


FIELD_GROUPS: dict[str, list[tuple[tuple[str, ...], str, str]]] = {
    "地块概况": [
        (("area_km2",), "面积", "km²"),
        (("dominant_landuse", "dominant_l"), "主导用地", ""),
        (("candidate_decision", "proposed_action"), "拟调整方向", ""),
    ],
    "POI与功能活跃度": [
        (("poi_total",), "POI总数", "个"),
        (("poi_density", "poi_densit"), "POI密度", "个/km²"),
        (("poi_industry", "poi_indust"), "产业POI", "个"),
        (("poi_commercial", "poi_commer"), "商业POI", "个"),
        (("poi_public",), "公共服务POI", "个"),
        (("poi_transport", "poi_transp"), "交通POI", "个"),
        (("poi_logistics", "poi_logist"), "物流POI", "个"),
    ],
    "人口与夜间灯光": [
        (("population", "pop_raw_su"), "人口", ""),
        (("pop_density", "Pop_densit", "pop_densit"), "人口密度", "人/km²"),
        (("night_light_mean", "NTL_MEAN"), "夜间灯光均值", ""),
        (("night_light_sum", "NTL_SUM"), "夜间灯光总量", ""),
        (("NTL_MAX", "night_light_max"), "夜间灯光最大值", ""),
    ],
    "地形条件": [
        (("dem_mean",), "平均高程", "m"),
        (("dem_relief",), "地形起伏度", "m"),
        (("slope_mean", "mean_slope", "slope_avg"), "平均坡度", "°"),
        (("slope_max", "max_slope"), "最大坡度", "°"),
    ],
    "道路可达性": [
        (("road_length", "road_lengt"), "道路总长度", "km"),
        (("road_density", "road_densi"), "道路密度", "km/km²"),
        (("main_road_density", "main_road_"), "主要道路密度", "km/km²"),
        (("distance_to_main_road", "dist_main_road", "road_distance"), "距主要道路", "m"),
    ],
    "发展与重大项目": [
        (("sec_prob",), "第二产业发展概率", ""),
        (("ter_prob",), "第三产业发展概率", ""),
        (("tra_prob",), "交通相关发展概率", ""),
        (("development_probability",), "模型发展概率", "%"),
        (("major_project", "key_project_name", "project_name"), "重大项目", ""),
        (("listed_in_key_project_list", "key_project"), "是否列入重大项目清单", ""),
        (("project_landing_probability", "major_project_probability"), "重大项目落地概率", "%"),
    ],
    "政策约束属性": [
        (("ecological_redline", "eco_redline"), "生态保护红线", ""),
        (("permanent_farmland", "basic_farmland"), "永久基本农田", ""),
        (("prohibited_disaster_zone",), "地质灾害禁建区", ""),
        (("drinking_water_source_level",), "饮用水水源保护级别", ""),
        (("military_restricted_zone",), "军事禁区", ""),
        (("planning_compliance", "land_use_plan_compliance"), "规划符合性", ""),
    ],
}


def _first(parcel: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lower = {str(key).lower(): key for key in parcel}
    for alias in aliases:
        key = alias if alias in parcel else lower.get(alias.lower())
        if key is not None and parcel.get(key) not in (None, ""):
            return parcel[key]
    return None


def summarize_parcel_attributes(parcel: dict[str, Any]) -> dict[str, Any]:
    """Create traceable, exact-value context for policy feasibility prompts."""

    groups: dict[str, list[dict[str, Any]]] = {}
    flat: dict[str, Any] = {}
    lines: list[str] = []
    for group_name, definitions in FIELD_GROUPS.items():
        items: list[dict[str, Any]] = []
        for aliases, label, unit in definitions:
            value = _first(parcel, aliases)
            if value is None or value == "":
                continue
            item = {"field": aliases[0], "label": label, "value": value, "unit": unit}
            items.append(item)
            flat[aliases[0]] = value
        if items:
            groups[group_name] = items
            rendered = "；".join(f"{item['label']}={item['value']}{item['unit']}" for item in items)
            lines.append(f"{group_name}：{rendered}")
    return {
        "block_id": str(parcel.get("block_id") or parcel.get("parcel_id") or ""),
        "block_name": parcel.get("block_name"),
        "groups": groups,
        "flat": flat,
        "available_field_count": len(flat),
        "text": "\n".join(lines),
    }
