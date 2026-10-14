from __future__ import annotations

from boundary_agent.models import (
    AdaptationResult,
    BlockDecision,
    BlockInput,
    ConstraintResult,
    SuitabilityResult,
)


def decide(
    block: BlockInput,
    suitability: SuitabilityResult,
    constraint: ConstraintResult,
    adaptation: AdaptationResult,
    config: dict,
    potential_thresholds: tuple[float, float] | None = None,
) -> BlockDecision:
    thresholds = config["thresholds"]
    score = adaptation.dynamic_score
    inclusion_score = adaptation.development_probability if potential_thresholds else score
    primary_threshold = potential_thresholds[0] if potential_thresholds else thresholds["primary_inclusion"]
    secondary_threshold = potential_thresholds[1] if potential_thresholds else thresholds["secondary_inclusion"]
    exclusion_threshold = _exclusion_development_threshold(config)
    development_score = adaptation.development_probability
    outside_boundary = _fully_outside_boundary(block)
    signal_completeness = max(0.0, 1.0 - len(adaptation.missing_components) / 10)
    confidence = min(suitability.confidence, 0.55 + 0.45 * signal_completeness)
    manual = (
        constraint.manual_review_required
        or suitability.data_completeness < thresholds["minimum_completeness"]
        or confidence < thresholds["minimum_confidence"]
    )

    if (
        block.inside_boundary
        and development_score < exclusion_threshold
        and constraint.policy_compliance != "不符合"
    ):
        decision = "建议调出"
    elif constraint.hard_constraint:
        decision = "人工复核"
    elif (
        not block.inside_boundary
        and outside_boundary
        and constraint.policy_compliance == "有条件符合"
        and inclusion_score >= secondary_threshold
    ):
        decision = "二级优先调入"
    elif (
        not block.inside_boundary
        and outside_boundary
        and inclusion_score >= primary_threshold
        and (potential_thresholds is not None or suitability.data_completeness >= 0.8)
    ):
        decision = "一级优先调入"
    elif outside_boundary and inclusion_score >= secondary_threshold:
        decision = "二级优先调入"
    elif (
        block.inside_boundary
        and thresholds["exclusion"] <= score < thresholds["secondary_inclusion"]
        and block.construction_ratio is not None
        and block.construction_ratio < 0.25
    ):
        decision = "内部更新"
    else:
        decision = "保持现状"

    decision_reasons = []
    if decision in {"一级优先调入", "二级优先调入"}:
        decision_reasons.append("地块位于现状城镇开发边界外")
    elif decision == "建议调出":
        decision_reasons.extend([
            "地块位于现状城镇开发边界内",
            f"dev_score/开发概率较低（{development_score / 100:.3f} < {exclusion_threshold / 100:.3f}）",
        ])
    policy_reasons = [constraint.policy_reason] if constraint.policy_reason else []
    reasons = (decision_reasons + policy_reasons + adaptation.key_drivers + suitability.opposing_evidence)[:5]
    risks = list(constraint.constraint_flags)
    if constraint.policy_compliance in {"有条件符合", "依据不足"}:
        risks.append(f"政策合规性：{constraint.policy_compliance}")
    if constraint.missing_constraints:
        risks.extend(constraint.missing_constraints)
    if constraint.policy_warning:
        risks.append(constraint.policy_warning)
    if suitability.missing_dimensions:
        risks.append("部分评价维度缺失")
    missing = list(dict.fromkeys(adaptation.missing_components + constraint.policy_requirements))
    status = "存在硬约束" if constraint.hard_constraint else "未发现硬约束" if not constraint.missing_constraints else "约束信息待核验"
    recommendation = {
        "一级优先调入": "可进入一级调入候选池，提交规划人员复核。",
        "二级优先调入": "可进入二级调入候选池，补充缺失信息后复核。",
        "建议调出": "位于开发边界内且dev_score较低，建议进入调出候选清单并复核现状利用效率。",
        "内部更新": "建议优先采用存量更新和功能优化，不新增边界外扩张。",
        "保持现状": "现有信息不足以支持边界调整，建议保持现状。",
        "人工复核": "存在硬约束或关键信息不足，必须由规划人员确认。",
    }[decision]
    if decision in {"一级优先调入", "二级优先调入"} and constraint.policy_compliance == "依据不足":
        recommendation = "仅保留为模型初筛候选；政策依据不足，不得认定为合规调入地块，须补充材料后复核。"
    elif decision in {"一级优先调入", "二级优先调入"} and constraint.policy_compliance == "有条件符合":
        recommendation = "可保留为条件性调入候选，完成政策前置条件和缺失材料核验后再进入规划审查。"
    return BlockDecision(
        block_id=block.block_id,
        block_name=block.block_name,
        inside_boundary=block.inside_boundary,
        boundary_overlap_ratio=float(block.boundary_overlap_ratio or 0),
        decision=decision,
        overall_suitability=suitability.overall_suitability,
        development_probability=adaptation.development_probability,
        planning_match=adaptation.planning_match,
        constraint_cost=adaptation.constraint_cost,
        dynamic_score=score,
        confidence=round(confidence, 3),
        constraint_status=status,
        key_reasons=reasons or ["当前有效指标不足，无法形成充分证据"],
        risks=risks,
        missing_information=missing,
        manual_review_required=manual or decision == "人工复核",
        recommendation=recommendation,
        recommended_use=adaptation.recommended_use,
        key_drivers=adaptation.key_drivers,
        policy_matched_rules=constraint.policy_matched_rules,
        policy_sources=constraint.policy_sources,
        policy_assessment=constraint.policy_assessment,
        policy_compliance=constraint.policy_compliance,
        policy_reason=constraint.policy_reason,
        policy_requirements=constraint.policy_requirements,
        rank_city=adaptation.rank_city,
        rank_district=adaptation.rank_district,
        rule_version=config["project"]["version"],
    )


def decide_all(blocks, suitability_results, constraint_results, adaptation_results, config):
    s_map = {item.block_id: item for item in suitability_results}
    c_map = {item.block_id: item for item in constraint_results}
    a_map = {item.block_id: item for item in adaptation_results}
    potential_thresholds = _development_potential_thresholds(blocks, config)
    return [
        decide(
            block,
            s_map[block.block_id],
            c_map[block.block_id],
            a_map[block.block_id],
            config,
            potential_thresholds,
        )
        for block in blocks
    ]


def _development_potential_thresholds(
    blocks: list[BlockInput],
    config: dict,
) -> tuple[float, float] | None:
    outside_blocks = [block for block in blocks if _fully_outside_boundary(block)]
    values = sorted(
        float(block.development_potential) * 100
        for block in outside_blocks
        if block.development_potential is not None
    )
    if len(values) != len(outside_blocks) or not values:
        return None
    settings = config.get("development_hotspot", {})
    primary_percentile = float(settings.get("primary_percentile", 0.95))
    secondary_percentile = float(settings.get("secondary_percentile", 0.80))

    def nearest_rank(percentile: float) -> float:
        bounded = max(0.0, min(1.0, percentile))
        index = min(
            len(values) - 1,
            max(0, int(bounded * (len(values) - 1) + 0.999999)),
        )
        return values[index]

    return nearest_rank(primary_percentile), nearest_rank(secondary_percentile)


def _exclusion_development_threshold(config: dict) -> float:
    settings = config.get("development_hotspot", {})
    configured = float(settings.get("exclusion_max_score", config["thresholds"]["exclusion"] / 100))
    return max(0.0, min(1.0, configured)) * 100


def _fully_outside_boundary(block: BlockInput) -> bool:
    if block.inside_boundary:
        return False
    if block.boundary_overlap_ratio is None:
        return True
    return float(block.boundary_overlap_ratio) <= 1e-9
