from __future__ import annotations

from collections import Counter
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from boundary_agent.agent.events import EventCallback, emit
from boundary_agent.config import load_config
from boundary_agent.llm.explainer import explain_with_llm
from boundary_agent.models import BlockInput, TaskOutput
from boundary_agent.tools.trained_model import (
    TrainedBoundaryModelError,
    enrich_blocks_with_model_predictions,
)
from boundary_agent.workflow.agents.constraint_agent import evaluate_constraints
from boundary_agent.workflow.agents.data_checker import validate_blocks
from boundary_agent.workflow.agents.adaptation_agent import evaluate_adaptation
from boundary_agent.workflow.agents.decision_agent import decide_all
from boundary_agent.workflow.agents.reviewer_agent import review_all
from boundary_agent.workflow.agents.suitability_agent import evaluate_blocks


class WorkflowState(TypedDict, total=False):
    task_id: str
    config: dict[str, Any]
    blocks: list[dict[str, Any]]
    validation: dict[str, Any]
    model_prediction: dict[str, Any]
    suitability: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    adaptation: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    include_ai_explanations: bool
    event_callback: EventCallback | None
    output: dict[str, Any]


def _models(state: WorkflowState) -> list[BlockInput]:
    return [BlockInput.model_validate(item) for item in state["blocks"]]


def validate_node(state: WorkflowState):
    emit(
        state.get("event_callback"),
        "action",
        "校验上传数据",
        f"检查 {len(state['blocks'])} 个地块的编号、指标和约束字段。",
    )
    return {"validation": validate_blocks(_models(state)).model_dump()}


def model_prediction_node(state: WorkflowState):
    emit(
        state.get("event_callback"),
        "action",
        "调用训练模型",
        "读取 XGBoost 模型并为上传地块计算开发概率。",
    )
    try:
        bundle = enrich_blocks_with_model_predictions(state["blocks"], state["config"])
        summary = {
            "enabled": True,
            "applied": bool(bundle.records),
            "model_dir": bundle.model_dir,
            "feature_count": bundle.feature_count,
            "missing_features": bundle.missing_features,
            "warning": bundle.warning,
        }
        emit(
            state.get("event_callback"),
            "observe",
            "模型预测完成",
            f"已生成 {len(bundle.blocks)} 个地块的开发概率，模型特征数 {bundle.feature_count}。",
            status="completed",
            metadata=summary,
        )
        return {"blocks": bundle.blocks, "model_prediction": summary}
    except TrainedBoundaryModelError as exc:
        emit(
            state.get("event_callback"),
            "observe",
            "训练模型回退",
            f"训练模型不可用，改用内置代理概率：{exc}",
            status="warning",
        )
        return {
            "model_prediction": {
                "enabled": True,
                "applied": False,
                "warning": f"训练模型不可用，已回退到工作流内置代理概率：{exc}",
            }
        }


def suitability_node(state: WorkflowState):
    emit(
        state.get("event_callback"),
        "action",
        "计算空间适宜性",
        "综合交通、POI、地形、人口和用地指标。",
    )
    results = evaluate_blocks(_models(state), state["config"])
    return {"suitability": [item.model_dump() for item in results]}


def constraint_node(state: WorkflowState):
    emit(
        state.get("event_callback"),
        "action",
        "检查空间约束",
        "识别生态红线、永久基本农田和灾害风险等限制。",
    )
    results = evaluate_constraints(_models(state), state["config"])
    return {"constraints": [item.model_dump() for item in results]}


def adaptation_node(state: WorkflowState):
    from boundary_agent.models import ConstraintResult, SuitabilityResult

    emit(
        state.get("event_callback"),
        "action",
        "计算动态适配指数",
        "合成模型概率、空间适宜性、规划匹配度和约束成本。",
    )
    results = evaluate_adaptation(
        _models(state),
        [SuitabilityResult.model_validate(item) for item in state["suitability"]],
        [ConstraintResult.model_validate(item) for item in state["constraints"]],
        state["config"],
    )
    return {"adaptation": [item.model_dump() for item in results]}


def decision_node(state: WorkflowState):
    from boundary_agent.models import AdaptationResult, ConstraintResult, SuitabilityResult
    emit(
        state.get("event_callback"),
        "action",
        "识别调整候选地块",
        "按动态适配指数和约束规则分类一级调入、二级调入、调出或保持现状。",
    )
    results = decide_all(
        _models(state),
        [SuitabilityResult.model_validate(item) for item in state["suitability"]],
        [ConstraintResult.model_validate(item) for item in state["constraints"]],
        [AdaptationResult.model_validate(item) for item in state["adaptation"]],
        state["config"],
    )
    return {"decisions": [item.model_dump() for item in results]}


def review_node(state: WorkflowState):
    from boundary_agent.models import BlockDecision, ConstraintResult, SuitabilityResult
    emit(
        state.get("event_callback"),
        "review",
        "复核规则结果",
        "检查硬约束、低置信度和缺失信息。",
    )
    results = review_all(
        [BlockDecision.model_validate(item) for item in state["decisions"]],
        [SuitabilityResult.model_validate(item) for item in state["suitability"]],
        [ConstraintResult.model_validate(item) for item in state["constraints"]],
        state["config"],
    )
    return {"reviews": [item.model_dump() for item in results]}


def llm_explanation_node(state: WorkflowState):
    if not state.get("include_ai_explanations", True):
        return {"decisions": state["decisions"]}
    decisions = []
    for item in state["decisions"]:
        updated = dict(item)
        updated["ai_explanation"] = explain_with_llm(item)
        decisions.append(updated)
    return {"decisions": decisions}


def report_node(state: WorkflowState):
    counts = Counter(item["decision"] for item in state["decisions"])
    ranked = sorted(
        state["decisions"],
        key=lambda item: (-float(item.get("dynamic_score", 0)), str(item.get("block_id", ""))),
    )
    output = TaskOutput(
        task_id=state["task_id"],
        project_version=state["config"]["project"]["version"],
        decisions=state["decisions"],
        reviews=state["reviews"],
        summary={
            "total": len(state["decisions"]),
            "decision_counts": dict(counts),
            "dynamic_score_average": round(
                sum(float(item.get("dynamic_score", 0)) for item in state["decisions"])
                / len(state["decisions"]),
                2,
            ) if state["decisions"] else 0,
            "top_candidate_ids": [item["block_id"] for item in ranked[:10]],
            "trained_model": state.get("model_prediction", {}),
        },
    )
    emit(
        state.get("event_callback"),
        "observe",
        "生成分析结果",
        "，".join(f"{name} {count} 个" for name, count in counts.items()) or "没有形成候选分类。",
        status="completed",
        metadata=output.summary,
    )
    return {"output": output.model_dump()}


def build_graph():
    builder = StateGraph(WorkflowState)
    builder.add_node("validate_input", validate_node)
    builder.add_node("predict_with_trained_model", model_prediction_node)
    builder.add_node("evaluate_suitability", suitability_node)
    builder.add_node("check_constraints", constraint_node)
    builder.add_node("calculate_dynamic_adaptation", adaptation_node)
    builder.add_node("classify_parcel", decision_node)
    builder.add_node("review_decision", review_node)
    builder.add_node("generate_ai_explanation", llm_explanation_node)
    builder.add_node("generate_report", report_node)
    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "predict_with_trained_model")
    builder.add_edge("predict_with_trained_model", "evaluate_suitability")
    builder.add_edge("evaluate_suitability", "check_constraints")
    builder.add_edge("check_constraints", "calculate_dynamic_adaptation")
    builder.add_edge("calculate_dynamic_adaptation", "classify_parcel")
    builder.add_edge("classify_parcel", "review_decision")
    builder.add_edge("review_decision", "generate_ai_explanation")
    builder.add_edge("generate_ai_explanation", "generate_report")
    builder.add_edge("generate_report", END)
    return builder.compile()


def run_workflow(
    blocks: list[dict],
    config: dict | None = None,
    include_ai_explanations: bool = True,
    event_callback: EventCallback | None = None,
) -> dict:
    graph = build_graph()
    state = graph.invoke({
        "task_id": str(uuid4()),
        "config": config or load_config(),
        "blocks": blocks,
        "include_ai_explanations": include_ai_explanations,
        "event_callback": event_callback,
    })
    return state["output"]
