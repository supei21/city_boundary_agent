from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


PolicyCompliance = Literal["符合", "有条件符合", "不符合", "依据不足"]


@dataclass(frozen=True)
class PolicyVerdict:
    status: PolicyCompliance
    reason: str
    requirements: list[str] = field(default_factory=list)
    supporting_rule_ids: list[str] = field(default_factory=list)
    blocking_rule_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FORBIDDEN_WORDS = ("禁止", "不得", "严禁", "不予", "不宜", "暂停")
_CONDITIONAL_WORDS = ("限制", "严控", "核验", "论证", "审批", "报批", "补划", "复核", "程序")


def judge_policy_compliance(
    parcel: dict[str, Any],
    rule_result: dict[str, Any],
    retrieval: dict[str, Any],
) -> PolicyVerdict:
    """Build a verdict from applicable rules; similarity alone never proves compliance."""

    matched = list(rule_result.get("matched_rules") or [])
    missing = [str(item) for item in parcel.get("_missing_policy_facts", [])]
    sources = list(retrieval.get("sources") or [])
    proposed_action = str(parcel.get("proposed_action") or "")

    blocking = [rule for rule in matched if _is_blocking(rule, proposed_action)]
    if blocking:
        actions = _unique(str(rule.get("action") or "政策禁止") for rule in blocking)
        return PolicyVerdict(
            status="不符合",
            reason="已命中禁止性政策规则：" + "；".join(actions[:3]),
            requirements=["不得直接纳入调入候选，需由规划人员核实适用条款和例外情形。"],
            blocking_rule_ids=_rule_ids(blocking),
        )

    restrictive = [
        rule
        for rule in matched
        if not _supports_action(rule, proposed_action) and _is_conditional(rule)
    ]
    restrictive_keys = {str(rule.get("rule_id")) for rule in restrictive}
    supportive = [
        rule
        for rule in matched
        if str(rule.get("rule_id")) not in restrictive_keys
        and (float(rule.get("score_adjustment", 0)) > 0 or _supports_action(rule, proposed_action))
    ]
    supportive_ids = _rule_ids(supportive)

    if restrictive:
        requirements = _requirements_from_rules(restrictive)
        requirements.extend(missing)
        return PolicyVerdict(
            status="有条件符合",
            reason="已命中限制性或程序性条款，须完成前置核验后才能纳入候选。",
            requirements=_unique(requirements),
            supporting_rule_ids=supportive_ids,
        )

    if supportive:
        if missing:
            return PolicyVerdict(
                status="有条件符合",
                reason="已命中正向政策情形，但关键底线与程序信息不完整。",
                requirements=_unique(missing),
                supporting_rule_ids=supportive_ids,
            )
        return PolicyVerdict(
            status="符合",
            reason="已命中可适用的正向政策规则，且当前底线信息未显示冲突。",
            supporting_rule_ids=supportive_ids,
        )

    requirements = list(missing)
    requirements.append("补充调整情形、项目类型、规划合规性等适用条件。")
    source_note = (
        "已检索到相关政策原文，但无规则满足当前地块事实。"
        if sources
        else "未检索到可用政策依据。"
    )
    return PolicyVerdict(
        status="依据不足",
        reason=source_note,
        requirements=_unique(requirements),
    )


def _is_blocking(rule: dict[str, Any], proposed_action: str) -> bool:
    if _supports_action(rule, proposed_action):
        return False
    score = float(rule.get("score_adjustment", 0))
    text = f"{rule.get('category', '')} {rule.get('action', '')}"
    return score <= -0.8 or any(word in text for word in _FORBIDDEN_WORDS)


def _supports_action(rule: dict[str, Any], proposed_action: str) -> bool:
    action = str(rule.get("action") or "")
    if proposed_action == "调出":
        return any(text in action for text in ("建议调出", "优先调出", "可按程序纳入调出"))
    if proposed_action == "调入":
        return any(text in action for text in ("允许调入", "优先调入", "可作为调入候选", "可增补候选"))
    return False


def _is_conditional(rule: dict[str, Any]) -> bool:
    score = float(rule.get("score_adjustment", 0))
    text = f"{rule.get('category', '')} {rule.get('clause_type', '')} {rule.get('action', '')}"
    return score < 0 or any(word in text for word in _CONDITIONAL_WORDS)


def _requirements_from_rules(rules: list[dict[str, Any]]) -> list[str]:
    return [
        f"落实规则{rule.get('rule_id')}要求：{rule.get('action')}"
        for rule in rules[:5]
    ]


def _rule_ids(rules: list[dict[str, Any]]) -> list[str]:
    return _unique(str(rule.get("rule_id")) for rule in rules if rule.get("rule_id"))


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
