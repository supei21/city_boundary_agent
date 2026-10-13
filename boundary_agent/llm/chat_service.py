from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from boundary_agent.agent.events import EventCallback, emit
from boundary_agent.config import load_config
from boundary_agent.agent.react_agent import run_tool_agent
from boundary_agent.llm.client import build_chat_model, model_is_ready
from boundary_agent.llm.policy_response import format_policy_answer, is_policy_query
from boundary_agent.models import BlockDecision, ChatIntent, ChatTurnResult
from boundary_agent.workflow.graph import run_workflow
from boundary_agent.tools.landscape_tools import (
    LandscapeEvaluator,
    LatestAnalysisProvider,
    build_landscape_tools,
)


INCLUSION_DECISIONS = {"一级优先调入", "二级优先调入"}
EXCLUSION_DECISIONS = {"建议调出"}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回JSON对象")
    return json.loads(cleaned[start : end + 1])


def _fallback_intent(query: str, blocks: list[dict]) -> ChatIntent:
    text = query.strip()
    lowered = text.lower()
    matched_ids = []
    matched_names = []
    for block in blocks:
        block_id = str(block.get("block_id", ""))
        block_name = str(block.get("block_name") or "")
        if block_id and block_id.lower() in lowered:
            matched_ids.append(block_id)
        if block_name and block_name in text:
            matched_names.append(block_name)

    if any(word in text for word in ("怎么用", "帮助", "能做什么", "使用方法")):
        action = "help"
    elif any(word in text for word in ("查看数据", "查看街区", "街区数据", "原始属性", "属性数据", "有哪些字段")):
        action = "show_data"
    elif any(word in text for word in ("形态评价", "景观指数", "边界完整", "边界连续", "破碎度")):
        action = "evaluate_landscape"
    elif any(word in text for word in ("比较", "对比", "区别")):
        action = "compare_blocks"
    elif any(word in text for word in ("调出", "移出", "退出边界")):
        action = "find_exclusion"
    elif any(word in text for word in ("调入", "纳入", "加入边界", "候选地块")):
        action = "find_inclusion"
    elif any(word in text for word in ("汇总", "概况", "整体", "全部情况")):
        action = "summarize_all"
    else:
        action = "analyze_blocks"

    inside = None
    if "边界外" in text:
        inside = False
    elif "边界内" in text:
        inside = True

    broad_action = action in {"find_inclusion", "find_exclusion", "summarize_all"}
    if action == "evaluate_landscape" and not matched_ids and not matched_names:
        broad_action = True
    if action == "show_data" and not matched_ids and not matched_names:
        broad_action = True
    analyze_all = (
        broad_action
        or any(word in text for word in ("所有", "全部", "哪些", "哪个"))
        or (inside is not None and "街区" in text and not matched_ids and not matched_names)
    )
    needs_target = action in {"analyze_blocks", "compare_blocks", "evaluate_landscape"} and not analyze_all
    clarification = needs_target and not matched_ids and not matched_names
    question = "请告诉我要分析的 block_id 或街区名称。" if clarification else None
    if action == "compare_blocks" and len(set(matched_ids + matched_names)) < 2:
        clarification = True
        question = "比较分析至少需要两个街区，请提供两个 block_id 或街区名称。"

    requested = []
    if action == "find_inclusion":
        requested = ["一级优先调入", "二级优先调入"]
    elif action == "find_exclusion":
        requested = ["建议调出"]

    return ChatIntent(
        action=action,
        block_ids=matched_ids,
        block_names=matched_names,
        inside_boundary=inside,
        requested_decisions=requested,
        analyze_all=analyze_all,
        confidence=0.65 if not clarification else 0.35,
        clarification_needed=clarification,
        clarification_question=question,
        parser="fallback",
    )


def parse_user_intent(query: str, blocks: list[dict], settings: dict[str, Any] | None = None) -> tuple[ChatIntent, list[str]]:
    """先用大模型理解意图；模型不可用或输出无效时使用可复现的本地解析。"""
    fallback = _fallback_intent(query, blocks)
    if not model_is_ready(settings):
        return fallback, ["大模型未启用或未配置Key，本次使用本地关键词解析意图。"]

    catalog = [
        {
            "block_id": block.get("block_id"),
            "block_name": block.get("block_name"),
            "inside_boundary": block.get("inside_boundary"),
        }
        for block in blocks[:500]
    ]
    prompt = f"""你是城镇开发边界街区分析系统的意图解析器。
只解析用户想做什么和涉及哪些街区，不进行规划判断，不编造街区。

允许的action：
- show_data：查看全部或指定街区的原始属性，不进行分析
- analyze_blocks：分析指定街区
- evaluate_landscape：评价指定街区调入或调出后对开发边界完整性、连续性和破碎度的影响
- find_inclusion：查找适合调入的街区
- find_exclusion：查找建议调出的街区
- compare_blocks：比较两个或多个街区
- summarize_all：汇总全部街区
- help：询问系统用法

返回一个JSON对象，字段必须包括：
action, block_ids, block_names, inside_boundary, requested_decisions,
analyze_all, confidence, clarification_needed, clarification_question。
inside_boundary只能是true、false或null。
requested_decisions只能使用：一级优先调入、二级优先调入、建议调出、内部更新、保持现状、人工复核。
如果用户没有给出分析对象且请求不是全局查询，应要求澄清。

可用街区目录：{json.dumps(catalog, ensure_ascii=False)}
用户输入：{query}
只返回JSON，不要Markdown。"""
    try:
        raw = build_chat_model(settings).invoke(prompt).content
        data = _extract_json(str(raw))
        data["parser"] = "llm"
        intent = ChatIntent.model_validate(data)
        return intent, []
    except Exception as exc:
        return fallback, [f"大模型意图解析失败，已使用本地解析：{exc}"]


def select_blocks(intent: ChatIntent, blocks: list[dict]) -> list[dict]:
    if intent.analyze_all or intent.action in {"find_inclusion", "find_exclusion", "summarize_all"}:
        selected = list(blocks)
    else:
        wanted_ids = {value.lower() for value in intent.block_ids}
        wanted_names = set(intent.block_names)
        selected = [
            block for block in blocks
            if str(block.get("block_id", "")).lower() in wanted_ids
            or str(block.get("block_name") or "") in wanted_names
        ]
    if intent.inside_boundary is not None:
        selected = [block for block in selected if block.get("inside_boundary") is intent.inside_boundary]
    return selected


def _fallback_answer(query: str, intent: ChatIntent, decisions: list[dict], total_selected: int) -> str:
    if intent.action == "help":
        return (
            "你可以这样提问：\n\n"
            "- `查看全部街区数据`\n"
            "- `查看 WH2025_DEMO001 的原始属性`\n"
            "- `分析 WH2025_DEMO001`\n"
            "- `用训练模型给候选地块打分`\n"
            "- `比较 WH2025_DEMO001 和 WH2025_DEMO003`\n"
            "- `哪些街区适合调入开发边界？`\n"
            "- `找出建议调出的边界内街区`\n"
            "- `评价 WH2025_DEMO001 调入后的边界形态`\n"
            "- `汇总全部街区的识别结果`"
        )
    if not decisions:
        return f"已分析 {total_selected} 个街区，但没有找到符合“{query}”条件的结果。你可以降低筛选范围或指定 block_id。"

    counts = Counter(item["decision"] for item in decisions)
    lines = [f"已分析 {total_selected} 个街区，当前返回 {len(decisions)} 个结果。"]
    if counts:
        lines.append("分类汇总：" + "，".join(f"{name} {count} 个" for name, count in counts.items()) + "。")
    if intent.action == "compare_blocks" and len(decisions) >= 2:
        ranking = sorted(decisions, key=lambda item: item.get("dynamic_score", 0), reverse=True)
        lines.append(
            "动态适配度从高到低："
            + " > ".join(f"{item['block_id']}（{item.get('dynamic_score', 0):.1f}）" for item in ranking)
            + "。"
        )
    for item in decisions[:10]:
        reasons = "；".join(item.get("key_reasons", [])[:2]) or "有效证据不足"
        policy = item.get("policy_compliance") or "未判读"
        lines.append(
            f"- **{item['block_id']}（{item.get('block_name') or '未命名'}）**："
            f"{item['decision']}，动态适配度 {item.get('dynamic_score', 0):.1f}，"
            f"适宜性 {item['overall_suitability']:.1f}，"
            f"置信度 {item['confidence']:.2f}，政策合规性：{policy}。依据：{reasons}。"
        )
    if len(decisions) > 10:
        lines.append(f"另外 {len(decisions) - 10} 个结果请查看下方结果表。")
    lines.append("以上为Demo辅助分析，不等同于正式规划结论。")
    return "\n\n".join(lines)


def _llm_answer(query: str, intent: ChatIntent, decisions: list[dict], total_selected: int, settings: dict[str, Any] | None = None) -> str:
    compact = [
        {
            "block_id": item["block_id"],
            "block_name": item.get("block_name"),
            "inside_boundary": item["inside_boundary"],
            "decision": item["decision"],
            "overall_suitability": item["overall_suitability"],
            "development_probability": item.get("development_probability"),
            "planning_match": item.get("planning_match"),
            "constraint_cost": item.get("constraint_cost"),
            "dynamic_score": item.get("dynamic_score"),
            "rank_city": item.get("rank_city"),
            "rank_district": item.get("rank_district"),
            "recommended_use": item.get("recommended_use"),
            "key_drivers": item.get("key_drivers"),
            "confidence": item["confidence"],
            "constraint_status": item["constraint_status"],
            "key_reasons": item["key_reasons"],
            "risks": item["risks"],
            "missing_information": item["missing_information"],
            "recommendation": item["recommendation"],
            "policy_compliance": item.get("policy_compliance"),
            "policy_reason": item.get("policy_reason"),
            "policy_requirements": item.get("policy_requirements"),
            "policy_matched_rules": item.get("policy_matched_rules"),
            "policy_sources": item.get("policy_sources"),
        }
        for item in decisions[:30]
    ]
    prompt = f"""你是城镇开发边界规划分析助手。请根据规则引擎的结构化结果回答用户。
不得修改decision、分数、约束、政策合规性和置信度，不得编造数据，不得把Demo结论称为正式规划结论。
必须区分模型候选等级与政策合规结论；policy_compliance为“依据不足”时不得表述为符合政策。
先直接回答，再简要说明政策依据、主要驱动因素、风险和缺失信息。若结果为空，要明确没有符合条件的街区。

用户问题：{query}
解析意图：{intent.model_dump_json()}
参与分析街区数：{total_selected}
规则结果：{json.dumps(compact, ensure_ascii=False)}
请使用简洁中文回答。"""
    return str(build_chat_model(settings).invoke(prompt).content)


def _handle_chat_fixed_flow(
    query: str,
    blocks: list[dict],
    config: dict | None = None,
    settings: dict[str, Any] | None = None,
    *,
    event_callback: EventCallback | None = None,
    landscape_evaluator: LandscapeEvaluator | None = None,
    latest_analysis_provider: LatestAnalysisProvider | None = None,
) -> ChatTurnResult:
    emit(event_callback, "understand", "理解任务", "使用本地可复现规则识别用户意图")
    intent, warnings = parse_user_intent(query, blocks, settings)
    emit(
        event_callback,
        "understand",
        "确定任务类型",
        f"识别为 {intent.action}，解析方式为 {intent.parser}",
        status="completed",
    )
    if intent.action == "help":
        emit(event_callback, "answer", "完成回答", "已整理可用操作示例", status="completed")
        return ChatTurnResult(
            intent=intent,
            response=_fallback_answer(query, intent, [], 0),
            model_used=intent.parser == "llm",
            warnings=warnings,
        )
    if intent.clarification_needed:
        emit(event_callback, "answer", "需要补充信息", intent.clarification_question, status="warning")
        return ChatTurnResult(
            intent=intent,
            response=intent.clarification_question or "请补充要分析的街区。",
            model_used=intent.parser == "llm",
            warnings=warnings,
        )

    selected = select_blocks(intent, blocks)
    emit(
        event_callback,
        "action",
        "选择街区数据",
        f"从当前数据中匹配到 {len(selected)} 个街区",
        status="completed",
    )
    if not selected:
        emit(event_callback, "error", "未找到街区", "请检查街区编号或筛选条件", status="warning")
        return ChatTurnResult(
            intent=intent,
            response="没有在当前数据中找到对应街区。请检查 block_id、街区名称或边界内外条件。",
            model_used=intent.parser == "llm",
            warnings=warnings,
        )

    if intent.action == "evaluate_landscape":
        if landscape_evaluator is None:
            message = "当前对话没有连接形态评价服务，请确认空间数据和城镇开发边界已经加载。"
            emit(event_callback, "error", "形态评价不可用", message, status="warning")
            return ChatTurnResult(
                intent=intent,
                response=message,
                selected_block_ids=[str(block["block_id"]) for block in selected],
                model_used=intent.parser == "llm",
                warnings=warnings + [message],
            )
        has_include = any(word in query for word in ("调入", "纳入", "加入边界"))
        has_exclude = any(word in query for word in ("调出", "移出", "退出边界"))
        action = "include" if has_include and not has_exclude else "exclude" if has_exclude and not has_include else "auto"
        target = "调入" if has_include and not has_exclude else "调出" if has_exclude and not has_include else "全部"
        explicit_target = bool(intent.block_ids or intent.block_names)
        selected_ids = [str(block["block_id"]) for block in selected] if explicit_target else []
        emit(
            event_callback,
            "action",
            "运行边界形态评价",
            (
                f"对 {len(selected_ids)} 个明确地块构造评价情景"
                if selected_ids
                else "读取最近一次调入调出分析中的候选地块"
            ),
        )
        holder: dict[str, dict[str, Any]] = {}
        morphology_tool = build_landscape_tools(
            {str(block.get("block_id")) for block in blocks if block.get("block_id") is not None},
            landscape_evaluator,
            latest_analysis_provider=latest_analysis_provider,
            on_result=lambda result: holder.update({"value": result}),
        )[0]
        tool_payload = json.loads(morphology_tool.invoke({
            "block_ids": selected_ids,
            "action": action,
            "target": target,
            "limit": len(selected_ids) if selected_ids else None,
        }))
        if tool_payload.get("error"):
            message = f"形态评价失败：{tool_payload['error']}"
            emit(event_callback, "error", "形态评价失败", str(tool_payload["error"]), status="error")
            return ChatTurnResult(
                intent=intent,
                response=message,
                selected_block_ids=tool_payload.get("selected_block_ids", selected_ids),
                model_used=intent.parser == "llm",
                warnings=warnings + [message],
            )
        landscape = holder["value"]
        selected_ids = [str(value) for value in tool_payload.get("selected_block_ids", [])]
        summary = landscape.get("summary") or {}
        lines = [
            f"已完成 {summary.get('evaluated_count', 0)} 个地块的边界形态评价。",
            (
                f"总体改善 {summary.get('improved_count', 0)} 个、基本稳定 "
                f"{summary.get('balanced_count', 0)} 个、总体恶化 {summary.get('worsened_count', 0)} 个。"
            ),
        ]
        for item in landscape.get("results", [])[:10]:
            lines.append(
                f"- **{item.get('block_id')}（{item.get('action_label')}）**："
                f"{item.get('overall_label')}。{item.get('conclusion')}"
            )
        lines.append("以上为Demo形态评价辅助判断，不等同于正式规划结论。")
        emit(
            event_callback,
            "observe",
            "形成形态评价结果",
            f"返回 {summary.get('evaluated_count', 0)} 个地块的六项指数和拓扑检查结果",
            status="completed",
        )
        emit(event_callback, "answer", "完成回答", "已整理形态评价结论", status="completed")
        return ChatTurnResult(
            intent=intent,
            response="\n\n".join(lines),
            selected_block_ids=selected_ids,
            workflow_summary=summary,
            landscape_evaluation=landscape,
            model_used=intent.parser == "llm",
            warnings=warnings,
        )

    if intent.action == "show_data":
        field_count = len({key for block in selected for key in block})
        emit(
            event_callback,
            "observe",
            "读取原始属性",
            f"获得 {len(selected)} 条记录和 {field_count} 个字段",
            status="completed",
        )
        emit(event_callback, "answer", "完成回答", "本次未运行适宜性评价", status="completed")
        return ChatTurnResult(
            intent=intent,
            response=(
                f"已找到 {len(selected)} 个街区，共包含 {field_count} 个不同字段。"
                "下面展示的是当前加载文件中的原始属性记录，本次没有运行适宜性或调入/调出分析。"
            ),
            selected_block_ids=[str(block["block_id"]) for block in selected],
            block_records=selected,
            model_used=intent.parser == "llm",
            warnings=warnings,
        )

    emit(event_callback, "action", "运行规则工作流", "执行数据校验、适宜性评价和空间约束审查")
    # 对话层会统一生成回答，因此这里关闭逐街区的大模型润色，避免重复调用。
    workflow_config = config or load_config()
    workflow = run_workflow(
        selected,
        workflow_config,
        include_ai_explanations=False,
        event_callback=event_callback,
    )
    trained_model = workflow.get("summary", {}).get("trained_model", {})
    model_warning = trained_model.get("warning")
    if model_warning:
        warnings.append(model_warning)
    decisions = list(workflow["decisions"])
    if intent.requested_decisions:
        wanted = set(intent.requested_decisions)
        decisions = [item for item in decisions if item["decision"] in wanted]
    elif intent.action == "find_inclusion":
        decisions = [item for item in decisions if item["decision"] in INCLUSION_DECISIONS]
    elif intent.action == "find_exclusion":
        decisions = [item for item in decisions if item["decision"] in EXCLUSION_DECISIONS]

    review_count = sum(bool(item.get("manual_review_required")) for item in decisions)
    emit(
        event_callback,
        "review",
        "审查分析结果",
        f"形成 {len(decisions)} 条结果，其中 {review_count} 条需要人工复核",
        status="completed",
    )

    used_model = intent.parser == "llm"
    if is_policy_query(query):
        response = format_policy_answer(query, decisions, len(selected))
    elif model_is_ready(settings):
        try:
            response = _llm_answer(query, intent, decisions, len(selected), settings)
            used_model = True
        except Exception as exc:
            warnings.append(f"大模型回答失败，已使用规则模板回答：{exc}")
            response = _fallback_answer(query, intent, decisions, len(selected))
    else:
        response = _fallback_answer(query, intent, decisions, len(selected))

    emit(event_callback, "answer", "完成回答", "已基于结构化规则结果整理结论", status="completed")

    return ChatTurnResult(
        intent=intent,
        response=response,
        selected_block_ids=[str(block["block_id"]) for block in selected],
        decisions=[BlockDecision.model_validate(item) for item in decisions],
        workflow_summary=workflow["summary"],
        model_used=used_model,
        warnings=warnings,
    )


def handle_chat(
    query: str,
    blocks: list[dict],
    config: dict | None = None,
    settings: dict[str, Any] | None = None,
    *,
    event_callback: EventCallback | None = None,
    landscape_evaluator: LandscapeEvaluator | None = None,
    latest_analysis_provider: LatestAnalysisProvider | None = None,
) -> ChatTurnResult:
    """模型可用时运行工具智能体；不可用时保留确定性的本地演示。"""
    workflow_config = config or load_config()
    if model_is_ready(settings):
        try:
            return run_tool_agent(
                query,
                blocks,
                workflow_config,
                settings,
                event_callback=event_callback,
                landscape_evaluator=landscape_evaluator,
                latest_analysis_provider=latest_analysis_provider,
            )
        except Exception as exc:
            emit(
                event_callback,
                "error",
                "模型工具流程不可用",
                "已自动切换到本地可复现流程",
                status="warning",
            )
            fallback = _handle_chat_fixed_flow(
                query,
                blocks,
                workflow_config,
                {"enabled": False},
                event_callback=event_callback,
                landscape_evaluator=landscape_evaluator,
                latest_analysis_provider=latest_analysis_provider,
            )
            fallback.warnings.insert(0, f"工具智能体调用失败，已切换本地兜底流程：{exc}")
            return fallback
    return _handle_chat_fixed_flow(
        query,
        blocks,
        workflow_config,
        settings,
        event_callback=event_callback,
        landscape_evaluator=landscape_evaluator,
        latest_analysis_provider=latest_analysis_provider,
    )
