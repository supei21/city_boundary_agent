from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from boundary_agent.models import BlockInput
from boundary_agent.rag.config import PolicyRagSettings, settings_from_config
from boundary_agent.rag.hard_constraints import evaluate_hard_constraints
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
        PolicyAssessment(enabled=True, available=False, warning="政策RAG未返回地块结果"),
    )


def assess_blocks_with_rag(blocks: list[BlockInput], config: dict[str, Any]) -> dict[str, PolicyAssessment]:
    settings = settings_from_config(config)
    if not settings.enabled:
        return {block.block_id: PolicyAssessment(enabled=False, available=False) for block in blocks}
    if not settings.policy_dir.exists():
        return {
            block.block_id: PolicyAssessment(
                enabled=True,
                available=False,
                warning=f"政策库目录不存在：{settings.policy_dir}",
            )
            for block in blocks
        }

    assessments: dict[str, PolicyAssessment] = {}
    try:
        for block in blocks:
            parcel = block_to_policy_parcel(block)
            hard = evaluate_hard_constraints(parcel)
            question = build_policy_question_for_parcel(parcel, block.block_id)
            sources = search_policy_documents(question, settings, settings.retrieval_k)
            assessments[block.block_id] = PolicyAssessment(
                enabled=True,
                available=True,
                rules={
                    "matched_rules": [],
                    "suggested_action": parcel.get("proposed_action"),
                    "hard_constraint_analysis": hard,
                },
                retrieval={
                    "question": question,
                    "sources": [_source_without_content(item) for item in sources],
                    "context": "\n\n".join(str(item.get("content") or "") for item in sources[:3]),
                },
            )
    except Exception as exc:
        return {
            block.block_id: PolicyAssessment(enabled=True, available=False, warning=f"政策RAG分析失败：{exc}")
            for block in blocks
        }
    return assessments


def block_to_policy_parcel(block: BlockInput) -> dict[str, Any]:
    extra = dict(block.model_extra or {})
    overlap_with: list[str] = []
    if block.ecological_redline:
        overlap_with.append("生态保护红线")
    if block.permanent_farmland:
        overlap_with.append("永久基本农田")
    if block.prohibited_disaster_zone:
        overlap_with.append("地质灾害禁建区")

    missing: list[str] = []
    if block.ecological_redline is None:
        missing.append("是否涉及生态保护红线")
    if block.permanent_farmland is None:
        missing.append("是否涉及永久基本农田")
    if block.prohibited_disaster_zone is None and block.disaster_risk is None:
        missing.append("地质灾害禁建区信息")
    if block.planning_match is None:
        missing.append("国土空间规划符合性")

    parcel = {
        **extra,
        "block_id": block.block_id,
        "block_name": block.block_name,
        "location": "城镇开发边界内" if block.inside_boundary else "城镇开发边界外",
        "proposed_action": "调出" if block.inside_boundary else "调入",
        "land_type": block.dominant_landuse or "未知",
        "overlap_with": overlap_with,
        "ecological_redline": block.ecological_redline,
        "permanent_farmland": block.permanent_farmland,
        "prohibited_disaster_zone": block.prohibited_disaster_zone,
        "disaster_risk": block.disaster_risk,
        "planning_compliance": None if block.planning_match is None else block.planning_match >= 60,
        "planning_match": block.planning_match,
        "development_probability": block.development_probability,
        "farmland_ratio": block.farmland_ratio,
        "ecological_ratio": block.ecological_ratio,
        "construction_ratio": block.construction_ratio,
        "_missing_policy_facts": missing,
    }
    return {key: value for key, value in parcel.items() if value is not None}


def build_policy_question(block: BlockInput, parcel: dict[str, Any]) -> str:
    return build_policy_question_for_parcel(parcel, block.block_id)


def build_policy_question_for_parcel(parcel: dict[str, Any], block_id: str | None = None) -> str:
    subject = f"地块{block_id}" if block_id else "候选地块"
    overlap = "、".join(map(str, parcel.get("overlap_with") or [])) or "未发现明确重叠"
    return (
        f"{subject}拟{parcel.get('proposed_action', '调整')}城镇开发边界，"
        f"现状用地为{parcel.get('land_type', '未知')}，空间约束为{overlap}。"
        "请检索调入调出、生态保护、永久基本农田、规划符合性和重大项目相关政策。"
    )


def _source_without_content(source: dict[str, Any]) -> dict[str, Any]:
    content = str(source.get("content") or "").strip().replace("\n", " ")
    compact = {key: value for key, value in source.items() if key != "content"}
    if content:
        compact["excerpt"] = content[:180] + ("..." if len(content) > 180 else "")
    return compact
