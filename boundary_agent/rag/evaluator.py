from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from boundary_agent.models import BlockInput
from boundary_agent.rag.config import PolicyRagSettings, settings_from_config
from boundary_agent.rag.judgment import judge_policy_compliance
from boundary_agent.rag.rule_engine import PolicyRuleEngine
from boundary_agent.rag.vector_store import search_policy_documents


@dataclass
class PolicyAssessment:
    enabled: bool
    available: bool
    rules: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None


def assess_block_with_rag(block: BlockInput, config: dict[str, Any]) -> PolicyAssessment:
    return assess_blocks_with_rag([block], config).get(
        block.block_id,
        PolicyAssessment(enabled=True, available=False, warning="政策RAG调用失败：未返回地块评估结果"),
    )


def assess_blocks_with_rag(blocks: list[BlockInput], config: dict[str, Any]) -> dict[str, PolicyAssessment]:
    settings = settings_from_config(config)
    block_ids = [block.block_id for block in blocks]
    if not settings.enabled:
        return {
            block_id: PolicyAssessment(enabled=False, available=False, warning="政策RAG未启用")
            for block_id in block_ids
        }
    if not settings.policy_dir.exists():
        return {
            block_id: PolicyAssessment(enabled=True, available=False, warning=f"政策库目录不存在：{settings.policy_dir}")
            for block_id in block_ids
        }

    try:
        engine = _get_rule_engine(settings.rules_path, _path_mtime(settings.rules_path))
        parcels = {block.block_id: block_to_policy_parcel(block) for block in blocks}
        rules_by_block = {
            block_id: engine.analyze(parcel)
            for block_id, parcel in parcels.items()
        }
        retrieval_by_signature = _retrieve_by_policy_signature(parcels, settings)
        assessments: dict[str, PolicyAssessment] = {}
        for block_id, parcel in parcels.items():
            retrieval = retrieval_by_signature[_policy_signature(parcel)]
            rules = rules_by_block[block_id]
            assessments[block_id] = PolicyAssessment(
                enabled=True,
                available=True,
                rules=rules,
                retrieval=retrieval,
                verdict=judge_policy_compliance(parcel, rules, retrieval).to_dict(),
            )
        return assessments
    except Exception as exc:
        return {
            block_id: PolicyAssessment(enabled=True, available=False, warning=f"政策RAG调用失败：{exc}")
            for block_id in block_ids
        }


def block_to_policy_parcel(block: BlockInput) -> dict[str, Any]:
    extra = dict(block.model_extra or {})
    overlap_with: list[str] = []
    if block.ecological_redline:
        overlap_with.append("生态保护红线")
    if block.permanent_farmland:
        overlap_with.append("永久基本农田")
    if block.prohibited_disaster_zone:
        overlap_with.append("地质灾害禁止建设区")
    if block.disaster_risk in {"高", "high"}:
        overlap_with.append("地质灾害高风险区")
    if block.disaster_risk in {"禁止建设", "prohibited"}:
        overlap_with.append("地质灾害极高风险区")

    land_type = block.dominant_landuse or "未知"
    if block.permanent_farmland:
        land_type = "永久基本农田"
    if block.ecological_redline:
        land_type = "生态保护红线"

    baseline_complete = True
    missing_policy_facts: list[str] = []
    if block.ecological_redline is None:
        baseline_complete = False
        missing_policy_facts.append("核实是否涉及生态保护红线。")
    if block.permanent_farmland is None:
        baseline_complete = False
        missing_policy_facts.append("核实是否涉及永久基本农田。")
    if block.prohibited_disaster_zone is None and block.disaster_risk is None:
        baseline_complete = False
        missing_policy_facts.append("核实地质灾害禁止建设区和风险等级。")
    if block.planning_match is None:
        missing_policy_facts.append("核实与国土空间总体规划的符合性。")

    parcel = {
        **extra,
        "block_id": block.block_id,
        "location": "城镇开发边界内" if block.inside_boundary else "城镇开发边界外",
        "proposed_action": "调出" if block.inside_boundary else "调入",
        "land_type": land_type,
        "overlap_with": overlap_with,
        "planning_compliance": None if block.planning_match is None else block.planning_match >= 60,
        "land_use_plan_compliance": None if block.planning_match is None else block.planning_match >= 60,
        "planning_match": block.planning_match,
        "development_probability": block.development_probability,
        "farmland_ratio": block.farmland_ratio,
        "ecological_ratio": block.ecological_ratio,
        "construction_ratio": block.construction_ratio,
        "scenario": getattr(block, "scenario", None),
        "project_type": getattr(block, "project_type", None),
        "project_level": getattr(block, "project_level", None),
        "listed_in_key_project_list": getattr(block, "listed_in_key_project_list", None),
        "disaster_risk_level": block.disaster_risk,
        "_missing_policy_facts": missing_policy_facts,
    }
    if baseline_complete:
        parcel["three_lines_constraints_breached"] = bool(overlap_with)
    return {key: value for key, value in parcel.items() if value is not None}


def build_policy_question(block: BlockInput, parcel: dict[str, Any]) -> str:
    return build_policy_question_for_parcel(parcel, block.block_id)


def build_policy_question_for_parcel(parcel: dict[str, Any], block_id: str | None = None) -> str:
    action = parcel.get("proposed_action", "调整")
    location = parcel.get("location", "")
    land_type = parcel.get("land_type", "")
    overlaps = "、".join(parcel.get("overlap_with") or ["无明确叠加约束"])
    subject = f"地块{block_id}" if block_id else "同类地块"
    missing = "、".join(parcel.get("_missing_policy_facts") or [])
    missing_text = f"当前待核实信息：{missing}" if missing else "当前关键底线信息已提供。"
    return (
        f"{subject}拟{action}城镇开发边界，位置为{location}，"
        f"主要用地类型为{land_type}，叠加约束为{overlaps}。"
        f"{missing_text}"
        "检索其是否符合城镇开发边界调整、局部优化、重大项目和三条控制线管控要求。"
    )


def _source_without_content(source: dict[str, Any]) -> dict[str, Any]:
    content = str(source.get("content") or "").strip().replace("\n", " ")
    compact = {key: value for key, value in source.items() if key != "content"}
    if content:
        compact["excerpt"] = content[:180] + ("..." if len(content) > 180 else "")
    return compact


def _retrieve_by_policy_signature(
    parcels: dict[str, dict[str, Any]],
    settings: PolicyRagSettings,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    retrievals: dict[tuple[Any, ...], dict[str, Any]] = {}
    for parcel in parcels.values():
        signature = _policy_signature(parcel)
        if signature in retrievals:
            continue
        question = build_policy_question_for_parcel(parcel)
        sources = search_policy_documents(question, settings, settings.retrieval_k)
        retrievals[signature] = {
            "question": question,
            "policy_signature": signature,
            "sources": [_source_without_content(item) for item in sources],
            "context": "\n\n".join(item["content"] for item in sources[:3]),
        }
    return retrievals


def _policy_signature(parcel: dict[str, Any]) -> tuple[Any, ...]:
    return (
        parcel.get("proposed_action"),
        parcel.get("location"),
        parcel.get("land_type"),
        tuple(sorted(str(item) for item in parcel.get("overlap_with", []))),
        parcel.get("planning_compliance"),
        parcel.get("scenario"),
        parcel.get("project_type"),
        parcel.get("project_level"),
        parcel.get("listed_in_key_project_list"),
        tuple(parcel.get("_missing_policy_facts") or []),
    )


@lru_cache(maxsize=8)
def _get_rule_engine(rules_path: Path, modified_at: float) -> PolicyRuleEngine:
    return PolicyRuleEngine(rules_path)


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
