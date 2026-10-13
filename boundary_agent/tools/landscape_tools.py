"""LangChain tool wrapper for deterministic boundary-morphology evaluation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import tool


LandscapeEvaluator = Callable[..., dict[str, Any]]
LandscapeResultCallback = Callable[[dict[str, Any]], None]
LatestAnalysisProvider = Callable[[], dict[str, Any] | None]
AGENT_DETAIL_LIMIT = 20

ADJUSTMENT_ACTIONS = {
    "一级优先调入": "include",
    "二级优先调入": "include",
    "建议调出": "exclude",
}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _json_safe_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    artifact = payload.get("artifact")
    if hasattr(artifact, "model_dump"):
        payload["artifact"] = artifact.model_dump(mode="json")
    return payload


def _compact_result(result: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    full_results = list(result.get("results", []))
    compact_results = []
    # The full result is retained through ``on_result`` for tables, maps and
    # artifacts.  Only a representative detail set is returned to the LLM so
    # an all-candidate run cannot exhaust the model context window.
    for item in full_results[:AGENT_DETAIL_LIMIT]:
        compact_results.append({
            "block_id": item.get("block_id"),
            "block_name": item.get("block_name"),
            "action": item.get("action"),
            "action_label": item.get("action_label"),
            "changed_area_km2": item.get("changed_area_km2"),
            "overall_grade": item.get("overall_grade"),
            "overall_label": item.get("overall_label"),
            "before": item.get("before", {}),
            "after": item.get("after", {}),
            "delta": item.get("delta", {}),
            "metric_assessments": item.get("metric_assessments", {}),
            "dimensions": item.get("dimensions", {}),
            "topology_check": item.get("topology_check", {}),
            "conclusion": item.get("conclusion"),
        })
    risk_block_ids = [
        str(item.get("block_id"))
        for item in full_results
        if (item.get("topology_check") or {}).get("risk")
    ]
    return {
        "tool": "evaluate_boundary_morphology",
        "selected_block_ids": [str(item.get("block_id")) for item in full_results],
        "missing_block_ids": missing,
        "summary": result.get("summary", {}),
        "results": compact_results,
        "detail_result_count": len(compact_results),
        "omitted_result_count": max(0, len(full_results) - len(compact_results)),
        "topology_risk_count": len(risk_block_ids),
        "topology_risk_block_ids": risk_block_ids,
    }


def build_landscape_tools(
    available_block_ids: set[str],
    landscape_evaluator: LandscapeEvaluator,
    latest_analysis_provider: LatestAnalysisProvider | None = None,
    on_result: LandscapeResultCallback | None = None,
) -> list:
    """Build the morphology tool around a backend-injected evaluator callback."""

    @tool
    def evaluate_boundary_morphology(
        block_ids: list[str] | None = None,
        action: Literal["include", "exclude", "auto"] = "auto",
        block_actions: dict[str, Literal["include", "exclude"]] | None = None,
        target: Literal["调入", "调出", "全部"] = "全部",
        limit: int | None = None,
    ) -> str:
        """基于最近一次调入调出分析，或按明确block_id与动作，评价开发边界形态。未给block_id时读取最近分析中的全部调入调出候选，不重新运行候选识别。"""

        normalized_limit = max(1, int(limit)) if limit is not None else None
        requested = list(dict.fromkeys(
            str(value).strip() for value in (block_ids or []) if str(value).strip()
        ))
        latest = latest_analysis_provider() if latest_analysis_provider is not None else None
        latest_decisions = list((latest or {}).get("decisions") or [])
        latest_by_id = {
            str(item.get("block_id")): item
            for item in latest_decisions
            if item.get("block_id") is not None
        }
        derived_actions: dict[str, str] = {}
        source = "explicit"

        if not requested:
            if not latest_decisions:
                return _dump({
                    "tool": "evaluate_boundary_morphology",
                    "error": "尚无可复用的调入调出分析结果，请先完成候选地块分析",
                    "selected_block_ids": [],
                })
            candidates = [
                item for item in latest_decisions
                if item.get("decision") in ADJUSTMENT_ACTIONS
                and (
                    target == "全部"
                    or target == "调入" and item.get("decision") in {"一级优先调入", "二级优先调入"}
                    or target == "调出" and item.get("decision") == "建议调出"
                )
            ]
            candidates.sort(key=lambda item: (
                int(item["rank_city"]) if item.get("rank_city") is not None else 10**9,
                -float(item.get("dynamic_score", 0)),
                str(item.get("block_id", "")),
            ))
            chosen = candidates[:normalized_limit] if normalized_limit is not None else candidates
            requested = [str(item["block_id"]) for item in chosen]
            derived_actions = {
                str(item["block_id"]): ADJUSTMENT_ACTIONS[str(item["decision"])]
                for item in chosen
            }
            source = "latest_analysis"
            if not requested:
                return _dump({
                    "tool": "evaluate_boundary_morphology",
                    "error": f"最近一次分析中没有可用于形态评价的{target}候选地块",
                    "selected_block_ids": [],
                })

        selected = [value for value in requested if value in available_block_ids]
        if normalized_limit is not None:
            selected = selected[:normalized_limit]
        missing = [value for value in requested if value not in available_block_ids]
        if not selected:
            return _dump({
                "tool": "evaluate_boundary_morphology",
                "error": "没有在当前空间数据中找到可评价的block_id",
                "selected_block_ids": [],
                "missing_block_ids": missing,
            })
        explicit_actions = {
            str(key): value
            for key, value in (block_actions or {}).items()
            if str(key) in selected and value in {"include", "exclude"}
        }
        per_block_actions = dict(derived_actions)
        per_block_actions.update(explicit_actions)
        if action == "auto" and not per_block_actions and latest_by_id:
            per_block_actions = {
                block_id: ADJUSTMENT_ACTIONS[str(latest_by_id[block_id]["decision"])]
                for block_id in selected
                if block_id in latest_by_id
                and latest_by_id[block_id].get("decision") in ADJUSTMENT_ACTIONS
            }
        if action == "auto" and len(per_block_actions) < len(selected):
            unresolved = [block_id for block_id in selected if block_id not in per_block_actions]
            return _dump({
                "tool": "evaluate_boundary_morphology",
                "error": "部分地块没有可继承的调入调出决策；请明确指定include或exclude动作",
                "selected_block_ids": selected,
                "unresolved_block_ids": unresolved,
                "missing_block_ids": missing,
            })
        try:
            raw_result = landscape_evaluator(
                block_ids=selected,
                block_actions=per_block_actions,
                action=action,
                neighborhood_m=None,
                cell_size_m=None,
                connectivity=None,
                limit=len(selected),
            )
        except (TypeError, ValueError) as exc:
            return _dump({
                "tool": "evaluate_boundary_morphology",
                "error": str(exc),
                "selected_block_ids": selected,
                "missing_block_ids": missing,
            })

        full_result = _json_safe_result(raw_result)
        if on_result is not None:
            on_result(full_result)
        compact = _compact_result(full_result, missing)
        compact["source"] = source
        compact["inherited_block_actions"] = per_block_actions
        return _dump(compact)

    return [evaluate_boundary_morphology]
