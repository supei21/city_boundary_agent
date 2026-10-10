"""把街区查询和规则分析包装成 LangChain 工具。"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import tool

from boundary_agent.agent.events import EventCallback
from boundary_agent.tools.trained_model import (
    TrainedBoundaryModelError,
    enrich_blocks_with_model_predictions,
    model_status,
)
from boundary_agent.workflow.graph import run_workflow


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def build_block_tools(
    blocks: list[dict],
    config: dict,
    event_callback: EventCallback | None = None,
    result_context: dict[str, Any] | None = None,
) -> list:
    """为当前已加载的数据构造工具；所有操作都只读。"""
    block_index = {str(item.get("block_id")): item for item in blocks}

    @tool
    def list_blocks(
        inside_boundary: bool | None = None,
    ) -> str:
        """列出可用街区的ID、名称和边界状态。可按是否位于开发边界内筛选。"""
        records = [
            {
                "block_id": item.get("block_id"),
                "block_name": item.get("block_name"),
                "inside_boundary": item.get("inside_boundary"),
            }
            for item in blocks
            if inside_boundary is None or item.get("inside_boundary") is inside_boundary
        ]
        return _dump({"tool": "list_blocks", "records": records})

    @tool
    def get_block_details(block_ids: list[str]) -> str:
        """按一个或多个block_id读取街区原始属性；只查看数据，不运行规划分析。"""
        records = [block_index[value] for value in block_ids if value in block_index]
        missing = [value for value in block_ids if value not in block_index]
        return _dump({
            "tool": "get_block_details",
            "selected_block_ids": [str(item["block_id"]) for item in records],
            "records": records,
            "missing_block_ids": missing,
        })

    @tool
    def score_blocks_with_trained_model(
        block_ids: list[str] | None = None,
        limit: int = 50,
    ) -> str:
        """调用训练完成的XGBoost模型，为地块计算开发变化概率；不运行规划分类。"""
        selected = blocks
        missing: list[str] = []
        if block_ids:
            selected = [block_index[value] for value in block_ids if value in block_index]
            missing = [value for value in block_ids if value not in block_index]
        if not selected:
            return _dump({
                "tool": "score_blocks_with_trained_model",
                "selected_block_ids": [],
                "records": [],
                "missing_block_ids": missing,
                "error": "没有找到可预测的block_id",
            })
        try:
            bundle = enrich_blocks_with_model_predictions(selected, config)
        except TrainedBoundaryModelError as exc:
            return _dump({
                "tool": "score_blocks_with_trained_model",
                "selected_block_ids": [],
                "records": [],
                "missing_block_ids": missing,
                "model_status": model_status(config),
                "error": f"训练模型执行失败：{exc}",
            })
        limit = max(1, min(limit, 200))
        return _dump({
            "tool": "score_blocks_with_trained_model",
            "selected_block_ids": [str(item.get("block_id")) for item in selected],
            "records": bundle.records[:limit],
            "missing_block_ids": missing,
            "model_status": {
                "available": True,
                "model_dir": bundle.model_dir,
                "feature_count": bundle.feature_count,
                "missing_features": bundle.missing_features,
                "source": bundle.source,
            },
        })

    @tool
    def analyze_blocks(block_ids: list[str]) -> str:
        """对指定街区运行训练模型预测、动态适配、约束和分类工作流。"""
        selected = [block_index[value] for value in block_ids if value in block_index]
        missing = [value for value in block_ids if value not in block_index]
        if not selected:
            return _dump({
                "tool": "analyze_blocks",
                "selected_block_ids": [],
                "decisions": [],
                "missing_block_ids": missing,
                "error": "没有找到可分析的block_id",
            })
        result = run_workflow(
            selected,
            config,
            include_ai_explanations=False,
            event_callback=event_callback,
        )
        if result_context is not None:
            result_context["workflow"] = result
            result_context["target"] = "指定地块"
        return _dump({
            "tool": "analyze_blocks",
            "selected_block_ids": [str(item["block_id"]) for item in selected],
            "decisions": result["decisions"],
            "summary": result["summary"],
            "missing_block_ids": missing,
        })

    @tool
    def identify_candidate_blocks(
        target: Literal["调入", "调出", "全部"] = "全部",
        limit: int = 50,
    ) -> str:
        """运行训练模型预测和动态适配工作流，再按动态适配指数返回排名靠前的候选；limit默认为50，最大200。"""
        result = run_workflow(
            blocks,
            config,
            include_ai_explanations=False,
            event_callback=event_callback,
        )
        if result_context is not None:
            result_context["workflow"] = result
            result_context["target"] = target
        decisions = list(result["decisions"])
        if target == "调入":
            decisions = [item for item in decisions if item["decision"] in {"一级优先调入", "二级优先调入"}]
        elif target == "调出":
            decisions = [item for item in decisions if item["decision"] == "建议调出"]
        decisions.sort(key=lambda item: (-float(item.get("dynamic_score", 0)), str(item["block_id"])))
        total_candidates = len(decisions)
        decisions = decisions[: max(1, min(limit, 200))]
        return _dump({
            "tool": "identify_candidate_blocks",
            "target": target,
            "selected_block_ids": [str(item["block_id"]) for item in blocks],
            "decisions": decisions,
            "summary": {
                **result["summary"],
                "matched_candidates": total_candidates,
            },
        })

    return [
        list_blocks,
        get_block_details,
        score_blocks_with_trained_model,
        analyze_blocks,
        identify_candidate_blocks,
    ]
