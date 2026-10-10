from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable


POLICY_QUERY_TERMS = (
    "政策",
    "合规",
    "条款",
    "规定",
    "法规",
    "能不能调",
    "是否可调",
    "允许调整",
)

_BLOCKING_WORDS = ("禁止", "不得", "严禁", "不予", "暂停")
_SUPPORTING_WORDS = ("允许", "优先", "支持", "可作为", "可按程序", "可纳入")


def is_policy_query(query: str) -> bool:
    """Return whether the user is explicitly asking for a policy judgment."""

    text = str(query or "").strip()
    return any(term in text for term in POLICY_QUERY_TERMS)


def format_policy_answer(
    query: str,
    decisions: Iterable[Any],
    total_selected: int,
) -> str:
    """Render policy evidence without allowing the LLM to alter the verdict."""

    items = [_as_dict(item) for item in decisions]
    if not items:
        return (
            "## 政策可行性结论：暂无可判读地块\n\n"
            f"本次选中 {total_selected} 个地块，但没有形成可用的政策判读结果。"
            "请指定地块编号，或检查筛选方向与地块边界位置是否一致。"
        )
    if len(items) == 1:
        return _format_single(query, items[0])
    return _format_multiple(query, items, total_selected)


def _format_single(query: str, item: dict[str, Any]) -> str:
    requested_action = _requested_action(query)
    applicable_action = "调出" if bool(item.get("inside_boundary")) else "调入"
    location = "城镇开发边界内" if bool(item.get("inside_boundary")) else "城镇开发边界外"
    status = str(item.get("policy_compliance") or "依据不足")
    hard_constraint = _has_hard_constraint(item)

    if requested_action and requested_action != applicable_action:
        conclusion = f"不适用{requested_action}判读"
        direction_note = (
            f"该地块当前位于{location}，应开展{applicable_action}或内部优化判读，"
            f"不能直接表述为{requested_action}候选。"
        )
    else:
        action = requested_action or applicable_action
        conclusion = _policy_gate_conclusion(status, action, hard_constraint)
        direction_note = f"该地块当前位于{location}，本次适用{action}方向的政策门控。"

    lines = [
        f"## 政策可行性结论：{conclusion}",
        "",
        f"- **分析对象**：{_block_label(item)}",
        f"- **调整方向**：{direction_note}",
        f"- **模型评价结果**：{item.get('decision') or '未形成候选分类'}",
        f"- **政策门控结果**：{status}",
        f"- **刚性约束**：{'存在' if hard_constraint else _constraint_text(item)}",
        f"- **政策支持程度**：{_support_level(status)}",
        f"- **政策判断理由**：{item.get('policy_reason') or '当前没有形成充分的政策判断理由。'}",
        "",
        "## 地块事实",
        "",
        f"- **边界位置**：{location}",
        f"- **适宜性评分**：{_number(item.get('overall_suitability'))}",
        f"- **开发概率**：{_number(item.get('development_probability'))}",
        f"- **规划匹配度**：{_number(item.get('planning_match'))}",
    ]
    facts = _unique(item.get("key_drivers") or item.get("key_reasons") or [])[:3]
    if facts:
        lines.append("- **主要评价证据**：" + "；".join(facts))

    lines.extend(["", "## 适用政策依据", ""])
    rules = list(item.get("policy_matched_rules") or [])
    if rules:
        for rule in rules[:5]:
            rule_id = rule.get("rule_id") or "未编号规则"
            policy_name = rule.get("policy_name") or "未标明政策名称"
            article = f"，{rule.get('article_ref')}" if rule.get("article_ref") else ""
            source = str(rule.get("source_text") or "").strip()
            action = str(rule.get("action") or "需结合地块事实复核").strip()
            lines.append(f"- **{rule_id}｜{policy_name}{article}**：{action}")
            if source:
                lines.append(f"  政策原文：{source}")
    else:
        lines.append("- 当前未匹配到能够直接适用于该地块事实的结构化政策规则。")

    _append_policy_sources(lines, [item])

    blocking = _blocking_items(item, rules)
    conditional = _unique(item.get("policy_requirements") or [])
    supporting = _supporting_rules(rules)
    missing = _unique(item.get("missing_information") or [])

    lines.extend(["", "## 禁止性约束", ""])
    if blocking:
        lines.extend(f"- {text}" for text in blocking)
    else:
        lines.append("- 未识别到明确禁止性约束。")

    lines.extend(["", "## 限制性条件", ""])
    if conditional:
        lines.extend(f"- {text}" for text in conditional)
    else:
        lines.append("- 当前未识别到需要单列的限制性前置条件。")

    lines.extend(["", "## 允许/支持条件", ""])
    if supporting:
        if blocking:
            lines.append("- 以下支持性依据不能覆盖已命中的禁止性约束：")
        lines.extend(f"- {text}" for text in supporting)
    else:
        lines.append("- 当前没有足以单独证明允许调整的正向规则；检索相似政策不等于满足适用条件。")

    lines.extend(["", "## 仍需核验", ""])
    if missing:
        lines.extend(f"- {text}" for text in missing)
    else:
        lines.append("- 当前结构化字段未提示新增待核验项，仍应由规划人员复核政策时效与适用层级。")
    lines.extend(["", "> 结论仅用于规划前期辅助判断，不替代法定审查、部门会商和审批结论。"])
    return "\n".join(lines)


def _format_multiple(query: str, items: list[dict[str, Any]], total_selected: int) -> str:
    statuses = Counter(str(item.get("policy_compliance") or "依据不足") for item in items)
    requested_action = _requested_action(query)
    direction = requested_action or "边界调整"
    hard_count = sum(_has_hard_constraint(item) for item in items)
    conclusion = _aggregate_conclusion(statuses, direction, hard_count, len(items))
    blocking = _unique(
        text
        for item in items
        for text in _blocking_items(item, list(item.get("policy_matched_rules") or []))
    )
    conditional = _unique(
        text for item in items for text in (item.get("policy_requirements") or [])
    )
    supporting = _unique(
        text
        for item in items
        for text in _supporting_rules(list(item.get("policy_matched_rules") or []))
    )
    missing = _unique(
        text for item in items for text in (item.get("missing_information") or [])
    )
    lines = [
        f"## 政策可行性结论：{conclusion}",
        "",
        f"- **政策门控**：{_aggregate_gate_text(statuses)}",
        f"- **是否存在刚性约束**：{'是' if hard_count else '当前未识别'}（{hard_count}/{len(items)} 个地块）",
        f"- **政策支持程度**：{_aggregate_support_level(statuses)}",
        "- **政策判断理由**：空间适宜或模型高分不等于政策允许调整；只有地块事实满足具体条款触发条件时，才能认定政策门控通过。",
        f"- **判读范围**：参与分析 {total_selected} 个地块，返回 {len(items)} 个候选结果。",
        "",
        "## 禁止性约束",
        "",
    ]
    if blocking:
        lines.extend(f"- {text}" for text in blocking[:8])
    else:
        lines.append("- 当前未识别到明确禁止性约束；仍需以完整三线数据进行最终核验。")

    lines.extend(["", "## 限制性条件", ""])
    if conditional:
        lines.extend(f"- {text}" for text in conditional[:8])
    else:
        lines.append("- 当前未识别到需要单列的限制性前置条件。")

    lines.extend(["", "## 允许/支持条件", ""])
    if supporting:
        if blocking:
            lines.append("- 以下支持性依据不能覆盖已命中的禁止性约束：")
        lines.extend(f"- {text}" for text in supporting[:8])
    else:
        lines.append("- 当前未命中满足地块事实的正向规则，不能仅凭检索到相关文件认定允许调整。")

    lines.extend(["", "## 仍需核验", ""])
    if missing:
        lines.extend(f"- {text}" for text in missing[:10])
    else:
        lines.append("- 复核政策时效、适用层级及地块权属和法定规划状态。")

    _append_policy_sources(lines, items)

    lines.extend(["", "## 地块政策判读摘要", ""])
    for item in items[:8]:
        status = str(item.get("policy_compliance") or "依据不足")
        applicable_action = "调出" if bool(item.get("inside_boundary")) else "调入"
        item_conclusion = _policy_gate_conclusion(status, applicable_action, _has_hard_constraint(item))
        lines.append(f"- **{_block_label(item)}**：{item_conclusion}；模型结果为“{item.get('decision') or '未分类'}”。")
    if len(items) > 8:
        lines.append(f"- 其余 {len(items) - 8} 个地块请在结果表中查看。")
    lines.extend(["", "> 结论仅用于规划前期辅助判断，不替代法定审查、部门会商和审批结论。"])
    return "\n".join(lines)


def _aggregate_conclusion(
    statuses: Counter,
    direction: str,
    hard_count: int,
    total: int,
) -> str:
    if hard_count == total or statuses.get("不符合", 0) == total:
        return f"存在禁止性约束，不得进入{direction}综合评价"
    if hard_count or statuses.get("不符合", 0):
        return f"存在禁止性与条件性差异，须分地块开展{direction}政策门控"
    if statuses.get("依据不足", 0) == total:
        return f"政策依据不足，暂不认定为合规{direction}候选"
    if statuses.get("符合", 0) == total:
        return f"政策门控通过，允许进入{direction}综合评价"
    if not statuses.get("依据不足", 0) and not statuses.get("不符合", 0):
        return f"满足前置条件后可进入{direction}综合评价"
    return f"政策支撑程度不一，须分地块开展{direction}政策门控"


def _aggregate_gate_text(statuses: Counter) -> str:
    return "，".join(f"{name} {count} 个" for name, count in statuses.items())


def _aggregate_support_level(statuses: Counter) -> str:
    total = sum(statuses.values())
    if statuses.get("符合", 0) == total:
        return "较强"
    if statuses.get("依据不足", 0) == total:
        return "不足"
    if statuses.get("不符合", 0) == total:
        return "无"
    return "中，且不同地块存在差异"


def _policy_gate_conclusion(status: str, action: str, hard_constraint: bool) -> str:
    if hard_constraint or status == "不符合":
        return f"禁止进入{action}综合评价"
    if status == "有条件符合":
        return f"满足前置条件后可进入{action}综合评价"
    if status == "符合":
        return f"允许进入{action}综合评价"
    return f"政策依据不足，暂不进入{action}综合评价"


def _requested_action(query: str) -> str | None:
    has_inclusion = "调入" in query
    has_exclusion = "调出" in query
    if has_inclusion and not has_exclusion:
        return "调入"
    if has_exclusion and not has_inclusion:
        return "调出"
    return None


def _has_hard_constraint(item: dict[str, Any]) -> bool:
    status = str(item.get("constraint_status") or "")
    return "存在硬约束" in status or str(item.get("policy_compliance") or "") == "不符合"


def _constraint_text(item: dict[str, Any]) -> str:
    status = str(item.get("constraint_status") or "")
    if "待核验" in status:
        return "待核验"
    return "未发现"


def _support_level(status: str) -> str:
    return {
        "符合": "较强",
        "有条件符合": "中",
        "不符合": "无",
        "依据不足": "不足",
    }.get(status, "不足")


def _blocking_items(item: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    values = []
    for rule in rules:
        action = str(rule.get("action") or "")
        if float(rule.get("score_adjustment", 0) or 0) <= -0.8 or any(word in action for word in _BLOCKING_WORDS):
            values.append(f"规则{rule.get('rule_id') or '未编号'}：{action}")
    for risk in item.get("risks") or []:
        text = str(risk)
        if (
            "硬约束" in text
            or any(word in text for word in _BLOCKING_WORDS)
            or (
                _has_hard_constraint(item)
                and any(term in text for term in ("生态保护红线", "永久基本农田", "禁止建设区"))
            )
        ):
            values.append(text)
    return _unique(values)


def _supporting_rules(rules: list[dict[str, Any]]) -> list[str]:
    values = []
    for rule in rules:
        action = str(rule.get("action") or "")
        score = float(rule.get("score_adjustment", 0) or 0)
        if score > 0 or any(word in action for word in _SUPPORTING_WORDS):
            values.append(f"规则{rule.get('rule_id') or '未编号'}：{action}")
    return _unique(values)


def _append_policy_sources(lines: list[str], items: list[dict[str, Any]]) -> None:
    sources: dict[str, dict[str, Any]] = {}
    for item in items:
        for source in item.get("policy_sources") or []:
            source_name = str(source.get("source") or "").strip()
            if not source_name:
                continue
            current = sources.get(source_name)
            if current is None or float(source.get("score", 0) or 0) > float(current.get("score", 0) or 0):
                sources[source_name] = dict(source)

    lines.extend(["", "## 匹配的政策文件", ""])
    if not sources:
        lines.append("- RAG未返回可定位的政策文件，请检查政策索引或扩大检索范围。")
        return

    ranked = sorted(
        sources.values(),
        key=lambda source: (
            str(source.get("type") or "") != "policy",
            -float(source.get("score", 0) or 0),
        ),
    )
    for source in ranked[:8]:
        source_name = str(source.get("source") or "")
        display_name = Path(source_name).name or source_name
        score = float(source.get("score", 0) or 0)
        source_type = "政策文件" if source.get("type") == "policy" else "结构化规则"
        article = f"；条款：{source.get('article_ref')}" if source.get("article_ref") else ""
        lines.append(
            f"- **{display_name}**（{source_type}；RAG相关度 {score:.3f}{article}）"
        )
        excerpt = str(source.get("excerpt") or "").strip()
        if excerpt:
            lines.append(f"  匹配摘要：{excerpt}")


def _block_label(item: dict[str, Any]) -> str:
    block_id = str(item.get("block_id") or "未编号地块")
    name = str(item.get("block_name") or "").strip()
    return f"{block_id}（{name}）" if name else block_id


def _number(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "待补充"


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    raise TypeError(f"Unsupported policy decision type: {type(item)!r}")


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
