from __future__ import annotations

from boundary_agent.models import BlockInput, ConstraintResult, SuitabilityResult


DIMENSION_LABELS = {
    "traffic": "交通条件",
    "terrain": "地形条件",
    "infrastructure": "基础设施",
    "urban_form": "城市形态",
    "industry": "产业布局",
    "population": "人口条件",
    "landuse": "土地利用条件",
}


def _cap(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _ratio(value: float | None, high: float) -> float | None:
    return None if value is None else _cap(value / high * 100)


def calculate_suitability(block: BlockInput, config: dict) -> SuitabilityResult:
    scales = config["scales"]
    weights = config["weights"]
    scores: dict[str, float] = {}

    traffic_parts = []
    if block.road_density is not None:
        traffic_parts.append(_ratio(block.road_density, scales["road_density_high"]))
    if block.poi_transport is not None:
        traffic_parts.append(_ratio(block.poi_transport, scales["transport_poi_high"]))
    if traffic_parts:
        scores["traffic"] = round(sum(traffic_parts) / len(traffic_parts), 2)

    if block.dem_relief is not None:
        scores["terrain"] = _cap(100 - block.dem_relief / scales["relief_high"] * 100)

    if block.poi_public is not None:
        scores["infrastructure"] = _ratio(block.poi_public, scales["public_poi_high"])

    if block.area_km2 is not None:
        scores["urban_form"] = 80.0 if 0.1 <= block.area_km2 <= 5 else 55.0

    industry_parts = []
    if block.poi_industry is not None:
        industry_parts.append(_ratio(block.poi_industry, scales["industry_poi_high"]))
    if block.poi_commercial is not None:
        industry_parts.append(_ratio(block.poi_commercial, scales["commercial_poi_high"]))
    if industry_parts:
        scores["industry"] = round(sum(industry_parts) / len(industry_parts), 2)

    if block.pop_density is not None:
        scores["population"] = _ratio(block.pop_density, scales["population_density_high"])

    landuse_parts = [
        value for value in (block.construction_ratio, block.industrial_ratio, block.residential_ratio)
        if value is not None
    ]
    if landuse_parts:
        scores["landuse"] = _cap(sum(landuse_parts) * 100)

    valid_weight = sum(weights[name] for name in scores)
    total_weight = sum(weights.values())
    overall = sum(scores[name] * weights[name] for name in scores) / valid_weight if valid_weight else 0.0
    completeness = valid_weight / total_weight if total_weight else 0.0
    distance_from_middle = abs(overall - 50) / 50
    confidence = min(1.0, completeness * (0.8 + 0.2 * distance_from_middle))

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    evidence = [f"{DIMENSION_LABELS[key]}得分较高（{value:.1f}）" for key, value in ranked[:3] if value >= 60]
    opposing = [f"{DIMENSION_LABELS[key]}得分偏低（{value:.1f}）" for key, value in sorted(scores.items(), key=lambda item: item[1])[:3] if value < 45]
    missing = [DIMENSION_LABELS[key] for key in weights if key not in scores]
    level = "高" if overall >= 80 else "较高" if overall >= 65 else "一般" if overall >= 40 else "低"
    return SuitabilityResult(
        block_id=block.block_id,
        dimension_scores=scores,
        overall_suitability=round(overall, 2),
        suitability_level=level,
        data_completeness=round(completeness, 3),
        confidence=round(confidence, 3),
        evidence=evidence,
        opposing_evidence=opposing,
        missing_dimensions=missing,
    )


def check_constraints(block: BlockInput, config: dict) -> ConstraintResult:
    flags: list[str] = []
    missing: list[str] = []
    if block.ecological_redline is True:
        flags.append("涉及生态保护红线")
    elif block.ecological_redline is None:
        missing.append("生态保护红线")
    if block.permanent_farmland is True:
        flags.append("涉及永久基本农田")
    elif block.permanent_farmland is None:
        missing.append("永久基本农田")
    disaster_prohibited = block.prohibited_disaster_zone is True or block.disaster_risk in {"禁止建设", "prohibited"}
    if disaster_prohibited:
        flags.append("位于地质灾害禁止建设区")
    elif block.prohibited_disaster_zone is None and block.disaster_risk is None:
        missing.append("地质灾害约束")
    hard = bool(flags)
    if disaster_prohibited or block.disaster_risk in {"高", "high"}:
        risk = "高"
    elif block.disaster_risk in {"中", "medium"}:
        risk = "中"
    else:
        risk = "低"
    return ConstraintResult(
        block_id=block.block_id,
        hard_constraint=hard,
        constraint_flags=flags,
        risk_level=risk,
        missing_constraints=missing,
        manual_review_required=hard or bool(missing),
    )
