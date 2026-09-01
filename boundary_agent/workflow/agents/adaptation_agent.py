from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from math import isfinite

from boundary_agent.models import (
    AdaptationResult,
    BlockInput,
    ConstraintResult,
    SuitabilityResult,
)


SIGNAL_LABELS = {
    "construction_change": "建设用地增长",
    "night_light_mean": "夜间灯光活跃度",
    "road_density": "道路可达性",
    "poi_density": "综合POI活跃度",
    "poi_industry": "产业集聚",
    "construction_ratio": "现状建设强度",
}


def _cap(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _percentile(ordered: list[float], value: float, fallback: float) -> float:
    if len(ordered) < 2 or ordered[0] == ordered[-1]:
        return _cap(fallback)
    left = bisect_left(ordered, value)
    right = bisect_right(ordered, value)
    average_rank = (left + right - 1) / 2
    return round(average_rank / (len(ordered) - 1) * 100, 2)


def _percentile_maps(blocks: list[BlockInput]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for field in SIGNAL_LABELS:
        values = []
        for block in blocks:
            value = getattr(block, field)
            if value is not None and isfinite(float(value)):
                values.append(float(value))
        ordered = sorted(values)
        if len(ordered) < 2 or ordered[0] == ordered[-1]:
            continue
        ranks: dict[str, float] = {}
        for block in blocks:
            value = getattr(block, field)
            if value is None:
                continue
            numeric = float(value)
            ranks[block.block_id] = _percentile(ordered, numeric, numeric)
        result[field] = ranks
    return result


def _constraint_cost(block: BlockInput, constraint: ConstraintResult) -> float:
    costs: list[float] = []
    for ratio in (block.ecological_ratio, block.farmland_ratio):
        if ratio is not None:
            costs.append(float(ratio) * 100)
    if block.ecological_redline is True or block.permanent_farmland is True:
        costs.append(100.0)
    if block.prohibited_disaster_zone is True or block.disaster_risk in {"禁止建设", "prohibited"}:
        costs.append(100.0)
    elif block.disaster_risk in {"高", "high"}:
        costs.append(80.0)
    elif block.disaster_risk in {"中", "medium"}:
        costs.append(40.0)
    if constraint.hard_constraint:
        costs.append(100.0)
    return _cap(max(costs, default=0.0))


def _recommended_use(block: BlockInput) -> str:
    industry = float(block.poi_industry or 0)
    service = float(block.poi_commercial or 0) + float(block.poi_public or 0)
    transport = float(block.poi_transport or 0) + float(block.poi_logistics or 0)
    if transport > max(industry, service) and transport > 0:
        return "交通与物流设施"
    if industry >= service and industry > 0:
        return "第二产业"
    if service > 0:
        return "第三产业与公共服务"
    return "待规划论证"


def evaluate_adaptation(
    blocks: list[BlockInput],
    suitability_results: list[SuitabilityResult],
    constraint_results: list[ConstraintResult],
    config: dict,
) -> list[AdaptationResult]:
    suitability_map = {item.block_id: item for item in suitability_results}
    constraint_map = {item.block_id: item for item in constraint_results}
    percentile_maps = _percentile_maps(blocks)
    ordered_suitability = sorted(item.overall_suitability for item in suitability_results)
    weights = config.get("dynamic_adaptation", {}).get(
        "weights",
        {
            "development_probability": 0.30,
            "suitability": 0.40,
            "planning_match": 0.30,
            "constraint_penalty": 0.15,
        },
    )

    results: list[AdaptationResult] = []
    for block in blocks:
        suitability = suitability_map[block.block_id]
        constraint = constraint_map[block.block_id]
        signal_scores = {
            field: ranks[block.block_id]
            for field, ranks in percentile_maps.items()
            if block.block_id in ranks
        }
        if block.development_probability is not None:
            probability = float(block.development_probability)
            probability_source = "已有开发概率"
        elif signal_scores:
            probability = sum(signal_scores.values()) / len(signal_scores)
            probability_source = "多源发展信号代理概率"
        else:
            probability = suitability.overall_suitability
            probability_source = "适宜性代理概率"

        planning_match = float(block.planning_match) if block.planning_match is not None else 50.0
        constraint_cost = _constraint_cost(block, constraint)
        normalized_suitability = _percentile(
            ordered_suitability,
            suitability.overall_suitability,
            suitability.overall_suitability,
        )
        dynamic_score = (
            probability * float(weights["development_probability"])
            + normalized_suitability * float(weights["suitability"])
            + planning_match * float(weights["planning_match"])
            - constraint_cost * float(weights["constraint_penalty"])
        )

        ranked_signals = sorted(signal_scores.items(), key=lambda item: item[1], reverse=True)
        drivers = [
            f"{SIGNAL_LABELS[field]}（百分位{score:.0f}）"
            for field, score in ranked_signals[:3]
            if score >= 60
        ]
        if block.planning_match is not None and planning_match >= 60:
            drivers.append(f"规划需求匹配度较高（{planning_match:.0f}）")
        drivers.extend(suitability.evidence[: max(0, 4 - len(drivers))])
        if not drivers:
            drivers.append(probability_source)

        missing = list(suitability.missing_dimensions)
        if block.development_probability is None:
            missing.append("历史样本训练的XGBoost开发概率")
        if block.planning_match is None:
            missing.append("规划知识匹配度")
        missing.extend(constraint.missing_constraints)

        results.append(
            AdaptationResult(
                block_id=block.block_id,
                development_probability=_cap(probability),
                suitability_score=normalized_suitability,
                planning_match=_cap(planning_match),
                constraint_cost=constraint_cost,
                dynamic_score=_cap(dynamic_score),
                component_weights={key: float(value) for key, value in weights.items()},
                key_drivers=list(dict.fromkeys(drivers))[:5],
                missing_components=list(dict.fromkeys(missing)),
                recommended_use=_recommended_use(block),
                rank_city=1,
                rank_district=1,
            )
        )

    city_order = sorted(results, key=lambda item: (-item.dynamic_score, item.block_id))
    city_ranks = {item.block_id: rank for rank, item in enumerate(city_order, start=1)}
    districts: dict[str, list[AdaptationResult]] = defaultdict(list)
    block_map = {block.block_id: block for block in blocks}
    for item in results:
        district = block_map[item.block_id].block_name or "未分区"
        districts[district].append(item)
    district_ranks: dict[str, int] = {}
    for items in districts.values():
        ordered = sorted(items, key=lambda item: (-item.dynamic_score, item.block_id))
        district_ranks.update(
            {item.block_id: rank for rank, item in enumerate(ordered, start=1)}
        )

    return [
        item.model_copy(
            update={
                "rank_city": city_ranks[item.block_id],
                "rank_district": district_ranks[item.block_id],
            }
        )
        for item in results
    ]
