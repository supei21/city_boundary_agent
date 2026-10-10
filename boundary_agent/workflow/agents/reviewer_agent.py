from __future__ import annotations

from boundary_agent.models import BlockDecision, ConstraintResult, ReviewResult, SuitabilityResult


def review(decision: BlockDecision, suitability: SuitabilityResult, constraint: ConstraintResult, config: dict) -> ReviewResult:
    issues = []
    if (
        decision.decision in {"一级优先调入", "二级优先调入"}
        and (decision.inside_boundary or decision.boundary_overlap_ratio > 1e-9)
    ):
        issues.append("与开发边界存在重叠的地块不应进入调入候选")
    if not decision.inside_boundary and decision.decision == "建议调出":
        issues.append("开发边界外地块不应进入调出候选")
    if constraint.hard_constraint and decision.decision in {"一级优先调入", "二级优先调入"}:
        issues.append("硬约束被调入结论绕过")
    if constraint.policy_compliance == "不符合" and decision.decision != "人工复核":
        issues.append("政策禁止结论被候选分类绕过")
    if (
        not decision.inside_boundary
        and constraint.policy_compliance == "依据不足"
        and decision.decision in {"一级优先调入", "二级优先调入"}
        and not decision.manual_review_required
    ):
        issues.append("政策依据不足时不应直接形成调入结论")
    if not decision.key_reasons:
        issues.append("缺少可追溯判断依据")
    if decision.confidence > suitability.data_completeness + 0.2:
        issues.append("置信度与数据完整度不一致")
    if issues:
        status = "revise"
    elif decision.manual_review_required:
        status = "human_review"
    else:
        status = "pass"
    return ReviewResult(block_id=decision.block_id, status=status, issues=issues)


def review_all(decisions, suitability_results, constraint_results, config):
    s_map = {item.block_id: item for item in suitability_results}
    c_map = {item.block_id: item for item in constraint_results}
    return [review(item, s_map[item.block_id], c_map[item.block_id], config) for item in decisions]
