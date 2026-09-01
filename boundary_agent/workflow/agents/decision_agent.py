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
) -> BlockDecision:
    thresholds = config["thresholds"]
    score = adaptation.dynamic_score
    signal_completeness = max(0.0, 1.0 - len(adaptation.missing_components) / 10)
    confidence = min(suitability.confidence, 0.55 + 0.45 * signal_completeness)
    manual = (
        constraint.manual_review_required
        or suitability.data_completeness < thresholds["minimum_completeness"]
        or confidence < thresholds["minimum_confidence"]
    )

    if constraint.hard_constraint:
        decision = "人工复核"
    elif not block.inside_boundary and score >= thresholds["primary_inclusion"] and suitability.data_completeness >= 0.8:
        decision = "一级优先调入"
    elif not block.inside_boundary and score >= thresholds["secondary_inclusion"]:
        decision = "二级优先调入"
    elif block.inside_boundary and score < thresholds["exclusion"] and constraint.risk_level == "高":
        decision = "建议调出"
    elif (
        block.inside_boundary
        and thresholds["exclusion"] <= score < thresholds["secondary_inclusion"]
        and block.construction_ratio is not None
        and block.construction_ratio < 0.25
    ):
        decision = "内部更新"
    else:
        decision = "保持现状"

    reasons = (adaptation.key_drivers + suitability.opposing_evidence)[:5]
    risks = list(constraint.constraint_flags)
    if constraint.missing_constraints:
        risks.append("规划约束信息不完整")
    if suitability.missing_dimensions:
        risks.append("部分评价维度缺失")
    missing = adaptation.missing_components
    status = "存在硬约束" if constraint.hard_constraint else "未发现硬约束" if not constraint.missing_constraints else "约束信息待核验"
    recommendation = {
        "一级优先调入": "可进入一级调入候选池，提交规划人员复核。",
        "二级优先调入": "可进入二级调入候选池，补充缺失信息后复核。",
        "建议调出": "建议进入调出候选清单，并核查灾害风险和现状利用效率。",
        "内部更新": "建议优先采用存量更新和功能优化，不新增边界外扩张。",
        "保持现状": "现有信息不足以支持边界调整，建议保持现状。",
        "人工复核": "存在硬约束或关键信息不足，必须由规划人员确认。",
    }[decision]
    return BlockDecision(
        block_id=block.block_id,
        block_name=block.block_name,
        inside_boundary=block.inside_boundary,
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
        rank_city=adaptation.rank_city,
        rank_district=adaptation.rank_district,
        rule_version=config["project"]["version"],
    )


def decide_all(blocks, suitability_results, constraint_results, adaptation_results, config):
    s_map = {item.block_id: item for item in suitability_results}
    c_map = {item.block_id: item for item in constraint_results}
    a_map = {item.block_id: item for item in adaptation_results}
    return [
        decide(block, s_map[block.block_id], c_map[block.block_id], a_map[block.block_id], config)
        for block in blocks
    ]
