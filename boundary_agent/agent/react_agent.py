"""A small, auditable ReAct loop for the boundary-analysis demo."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from boundary_agent.agent.events import EventCallback, emit
from boundary_agent.llm.client import build_chat_model
from boundary_agent.llm.policy_response import format_policy_answer, is_policy_query
from boundary_agent.models import BlockDecision, ChatIntent, ChatTurnResult
from boundary_agent.tools import build_block_tools, build_landscape_tools
from boundary_agent.tools.landscape_tools import LandscapeEvaluator, LatestAnalysisProvider


SYSTEM_PROMPT = """你是城镇开发边界增量空间动态适配智能体。
你需要根据用户问题选择工具，并只依据工具返回的数据组织回答。

工具使用规则：
1. 查询街区目录使用 list_blocks。
2. 查看原始字段使用 get_block_details，不要运行评价。
3. 只需要模型开发概率时使用 score_blocks_with_trained_model。若上传数据包含dev_score，优先读取该综合发展潜力；否则调用训练完成的XGBoost模型。
4. 分析或比较指定街区使用 analyze_blocks，工具会优先使用dev_score或训练模型补充development_probability，再比较dynamic_score、rank_city和key_drivers。
5. 查找调入、调出候选或总体分析使用 identify_candidate_blocks，工具结果已经完成模型预测、空间评价、动态适配和规则核验。
6. 评价地块调入或调出后对开发边界完整性、连续性和破碎度的影响，使用 evaluate_boundary_morphology。不得自行估算NP、PD、LPI、ED、LSI或AI；“调入”使用include，“调出”使用exclude，不明确时使用auto。
7. 用户提到“当前、已有、上一次”的候选地块形态评价时，直接调用 evaluate_boundary_morphology 且不传block_ids和limit；该工具会读取最近一次调入调出分析，并评价全部一级调入、二级调入和建议调出地块，不得先调用 identify_candidate_blocks，不得重新运行XGBoost。只有用户明确要求限定数量时才传limit。
8. 只有用户明确要求“重新分析、重新识别、先识别再评价”时，才先调用 identify_candidate_blocks，再把得到的调入调出候选及其动作传给 evaluate_boundary_morphology。
9. block_id 不明确且最近一次分析也没有结果时，应说明需要先完成候选地块分析，禁止编造街区、分数和约束结论。
10. 如果本次只使用 list_blocks 或 get_block_details，只能展示原始字段，不得推断调入、调出、适宜性或风险等级。
11. 工具返回的 policy_compliance、policy_reason 和 policy_requirements 是政策RAG判读结果。必须区分“符合”“有条件符合”“不符合”和“依据不足”，不得将依据不足表述为符合。
12. 调入与调出必须严格区分：边界外地块适用调入判读，边界内地块适用调出或内部优化判读，禁止把两个方向拼接在同一结论中。

获得足够工具证据后直接给出简洁中文结论。候选识别任务说明政策合规性、引用规则、规划匹配、主要驱动因素、风险和缺失信息；形态评价任务说明六项指数变化、三个评价维度和拓扑风险。只解释工具实际返回的字段。
必须声明结果属于规则Demo辅助判断，不是正式规划结论。
不要输出隐藏思维链；界面只展示工具行动、观察和审查等公开执行信息。
"""


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content).strip()


def _reuses_existing_analysis(query: str) -> bool:
    """Morphology evaluation is downstream unless the user explicitly requests a rerun."""

    morphology_words = ("形态评价", "边界形态", "景观指数", "边界完整", "边界连续", "破碎度")
    rerun_words = ("重新分析", "重新识别", "先识别再评价", "重新运行候选", "重新跑候选")
    return any(word in query for word in morphology_words) and not any(word in query for word in rerun_words)


def _intent_from_tools(tool_names: list[str]) -> ChatIntent:
    if "evaluate_boundary_morphology" in tool_names:
        action = "evaluate_landscape"
    elif "identify_candidate_blocks" in tool_names:
        action = "summarize_all"
    elif "analyze_blocks" in tool_names:
        action = "analyze_blocks"
    elif "score_blocks_with_trained_model" in tool_names:
        action = "show_data"
    elif "get_block_details" in tool_names or "list_blocks" in tool_names:
        action = "show_data"
    else:
        action = "help"
    return ChatIntent(action=action, confidence=0.9, parser="llm")


def _observation_summary(tool_name: str, payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return str(payload["error"])
    if tool_name in {"list_blocks", "get_block_details", "score_blocks_with_trained_model"}:
        return f"读取到 {len(payload.get('records', []))} 条街区记录"
    if tool_name == "evaluate_boundary_morphology":
        summary = payload.get("summary") or {}
        risk_count = sum(
            bool(item.get("topology_check", {}).get("risk"))
            for item in payload.get("results", [])
        )
        return (
            f"完成 {summary.get('evaluated_count', len(payload.get('results', [])))} 个地块形态评价，"
            f"改善 {summary.get('improved_count', 0)} 个、稳定 {summary.get('balanced_count', 0)} 个、"
            f"恶化 {summary.get('worsened_count', 0)} 个，拓扑风险 {risk_count} 个"
        )
    decisions = payload.get("decisions", [])
    summary = payload.get("summary") or {}
    trained_model = summary.get("trained_model") or {}
    model_text = ""
    if trained_model.get("applied"):
        model_text = f"，训练模型已调用（{trained_model.get('feature_count', 0)} 个特征）"
    elif trained_model.get("warning"):
        model_text = "，训练模型未应用并已回退"
    return f"完成 {len(payload.get('selected_block_ids', []))} 个街区分析，返回 {len(decisions)} 条决策{model_text}"


def _merge_payload(
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    selected_ids: list[str],
) -> dict[str, Any]:
    records.extend(payload.get("records", []))
    decisions.extend(payload.get("decisions", []))
    selected_ids.extend(str(value) for value in payload.get("selected_block_ids", []))
    return payload.get("summary") or {}


def run_tool_agent(
    query: str,
    blocks: list[dict],
    config: dict,
    settings: dict[str, Any] | None = None,
    *,
    event_callback: EventCallback | None = None,
    landscape_evaluator: LandscapeEvaluator | None = None,
    latest_analysis_provider: LatestAnalysisProvider | None = None,
    max_steps: int = 4,
) -> ChatTurnResult:
    """Run a bounded ReAct loop using structured tool calls.

    The model's private reasoning is not persisted. Public events describe only
    the selected action, tool observation and deterministic review result.
    """

    emit(event_callback, "understand", "理解任务", "正在判断问题需要哪些街区数据与分析工具")

    landscape_result_holder: dict[str, dict[str, Any]] = {}
    result_context: dict[str, Any] = {}
    tools = build_block_tools(
        blocks,
        config,
        event_callback=event_callback,
        result_context=result_context,
    )
    if _reuses_existing_analysis(query):
        model_workflow_tools = {
            "score_blocks_with_trained_model",
            "analyze_blocks",
            "identify_candidate_blocks",
        }
        tools = [item for item in tools if item.name not in model_workflow_tools]
    if landscape_evaluator is not None:
        tools.extend(build_landscape_tools(
            {str(item.get("block_id")) for item in blocks if item.get("block_id") is not None},
            landscape_evaluator,
            latest_analysis_provider=latest_analysis_provider,
            on_result=lambda result: landscape_result_holder.update({"value": result}),
        ))
    tool_map = {tool.name: tool for tool in tools}
    model = build_chat_model(settings).bind_tools(tools)
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=query)]

    trace: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    workflow_summary: dict[str, Any] = {}
    tool_names: list[str] = []
    warnings: list[str] = []
    final_message: AIMessage | None = None

    for step in range(1, max_steps + 1):
        response = model.invoke(messages)
        if not isinstance(response, AIMessage):
            raise TypeError("模型没有返回有效的 AIMessage")
        messages.append(response)

        if not response.tool_calls:
            final_message = response
            break

        for call in response.tool_calls:
            tool_name = str(call.get("name", ""))
            tool_args = call.get("args") or {}
            call_id = str(call.get("id") or f"tool-{step}-{len(trace) + 1}")
            tool_names.append(tool_name)
            trace_item = {
                "step": step,
                "tool": tool_name,
                "input": tool_args,
                "status": "called",
            }
            trace.append(trace_item)
            emit(
                event_callback,
                "action",
                f"调用 {tool_name}",
                "使用结构化参数执行街区工具",
                metadata={"step": step, "tool": tool_name, "input": tool_args},
            )

            tool = tool_map.get(tool_name)
            if tool is None:
                payload = {"error": f"未注册工具：{tool_name}"}
                content = json.dumps(payload, ensure_ascii=False)
                trace_item["status"] = "error"
            else:
                try:
                    content = str(tool.invoke(tool_args))
                    payload = json.loads(content)
                    trace_item["status"] = "completed"
                except Exception as exc:  # Tool errors become observations for the next step.
                    payload = {"error": f"工具执行失败：{exc}"}
                    content = json.dumps(payload, ensure_ascii=False)
                    trace_item["status"] = "error"
                    warnings.append(payload["error"])

            summary = _merge_payload(payload, records, decisions, selected_ids)
            if summary:
                workflow_summary = summary
            observation = _observation_summary(tool_name, payload)
            emit(
                event_callback,
                "observe",
                "获得工具结果",
                observation,
                status="error" if payload.get("error") else "completed",
                metadata={"step": step, "tool": tool_name},
            )
            messages.append(ToolMessage(content=content, tool_call_id=call_id))

    if final_message is None:
        raise RuntimeError(f"ReAct循环达到最大步数 {max_steps}，尚未生成最终回答")

    unique_records = {str(item.get("block_id")): item for item in records}
    unique_decisions = {str(item.get("block_id")): item for item in decisions}
    cached_workflow = result_context.get("workflow")
    cached_target = result_context.get("target")
    if isinstance(cached_workflow, dict) and "identify_candidate_blocks" in tool_names:
        cached_decisions = list(cached_workflow.get("decisions", []))
        if cached_target == "调入":
            cached_decisions = [
                item
                for item in cached_decisions
                if item.get("decision") in {"一级优先调入", "二级优先调入"}
            ]
        elif cached_target == "调出":
            cached_decisions = [
                item for item in cached_decisions if item.get("decision") == "建议调出"
            ]
        # Keep the tool response small for the model, but return the complete
        # workflow result to downstream spatial rendering.  The target filter is
        # only for the conversational answer, not for the map.
        cached_decisions = list(cached_workflow.get("decisions", []))
        unique_decisions = {
            str(item.get("block_id")): item for item in cached_decisions
        }
        workflow_summary = cached_workflow.get("summary") or workflow_summary
    validated_decisions = [BlockDecision.model_validate(item) for item in unique_decisions.values()]
    review_count = sum(item.manual_review_required for item in validated_decisions)
    if validated_decisions:
        emit(
            event_callback,
            "review",
            "审查分析结果",
            f"校验 {len(validated_decisions)} 条决策，其中 {review_count} 条需要人工复核",
            status="completed",
        )

    read_only_tools = {"list_blocks", "get_block_details"}
    if is_policy_query(query) and validated_decisions:
        response_text = format_policy_answer(
            query,
            validated_decisions,
            len(set(selected_ids)) or len(validated_decisions),
        )
    elif tool_names and set(tool_names).issubset(read_only_tools):
        field_names = sorted({key for item in unique_records.values() for key in item})
        field_text = "、".join(field_names[:20])
        suffix = "等" if len(field_names) > 20 else ""
        response_text = (
            f"已读取 {len(unique_records)} 个街区的原始属性，共 {len(field_names)} 个字段。"
            f"主要包括：{field_text}{suffix}。\n\n"
            "详细记录已加入右侧文件预览。本次仅查看原始数据，没有运行适宜性评价或调入调出判断。"
        )
    else:
        response_text = _message_text(final_message) or "智能体已完成工具调用，但没有生成文字回答。"
    emit(event_callback, "answer", "完成回答", "已基于工具证据整理结论", status="completed")
    return ChatTurnResult(
        intent=_intent_from_tools(tool_names),
        response=response_text,
        selected_block_ids=list(dict.fromkeys(selected_ids)),
        block_records=list(unique_records.values()),
        decisions=validated_decisions,
        workflow_summary=workflow_summary,
        landscape_evaluation=landscape_result_holder.get("value"),
        model_used=True,
        warnings=warnings,
        tool_trace=trace,
    )
