from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlockInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    block_id: str = Field(min_length=1)
    block_name: str | None = None
    inside_boundary: bool
    area_km2: float | None = Field(default=None, gt=0)
    centroid_lon: float | None = Field(default=None, ge=-180, le=180)
    centroid_lat: float | None = Field(default=None, ge=-90, le=90)
    poi_density: float | None = Field(default=None, ge=0)
    poi_industry: float | None = Field(default=None, ge=0)
    poi_commercial: float | None = Field(default=None, ge=0)
    poi_public: float | None = Field(default=None, ge=0)
    poi_transport: float | None = Field(default=None, ge=0)
    poi_logistics: float | None = Field(default=None, ge=0)
    dem_mean: float | None = None
    dem_relief: float | None = Field(default=None, ge=0)
    population: float | None = Field(default=None, ge=0)
    pop_density: float | None = Field(default=None, ge=0)
    road_density: float | None = Field(default=None, ge=0)
    main_road_density: float | None = Field(default=None, ge=0)
    secondary_road_density: float | None = Field(default=None, ge=0)
    branch_road_density: float | None = Field(default=None, ge=0)
    dominant_landuse: str | None = None
    construction_ratio: float | None = Field(default=None, ge=0, le=1)
    previous_construction_ratio: float | None = Field(default=None, ge=0, le=1)
    construction_change: float | None = Field(default=None, ge=-1, le=1)
    industrial_ratio: float | None = Field(default=None, ge=0, le=1)
    residential_ratio: float | None = Field(default=None, ge=0, le=1)
    night_light_mean: float | None = Field(default=None, ge=0)
    night_light_sum: float | None = Field(default=None, ge=0)
    boundary_overlap_ratio: float | None = Field(default=None, ge=0, le=1)
    farmland_ratio: float | None = Field(default=None, ge=0, le=1)
    ecological_ratio: float | None = Field(default=None, ge=0, le=1)
    planning_match: float | None = Field(default=None, ge=0, le=100)
    development_potential: float | None = Field(default=None, ge=0, le=1)
    development_probability: float | None = Field(default=None, ge=0, le=100)
    sec_prob: float | None = Field(default=None, ge=0, le=1)
    ter_prob: float | None = Field(default=None, ge=0, le=1)
    tra_prob: float | None = Field(default=None, ge=0, le=1)
    data_status: str | None = None
    disaster_risk: Literal["低", "中", "高", "禁止建设", "low", "medium", "high", "prohibited"] | None = None
    ecological_redline: bool | None = None
    permanent_farmland: bool | None = None
    prohibited_disaster_zone: bool | None = None

    @field_validator("block_id")
    @classmethod
    def strip_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("block_id不能为空")
        return value


class ValidationResult(BaseModel):
    valid: bool
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usable_indicators: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)


class SuitabilityResult(BaseModel):
    block_id: str
    dimension_scores: dict[str, float]
    overall_suitability: float = Field(ge=0, le=100)
    suitability_level: str
    data_completeness: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    opposing_evidence: list[str]
    missing_dimensions: list[str]


class ConstraintResult(BaseModel):
    block_id: str
    hard_constraint: bool
    constraint_flags: list[str]
    risk_level: str
    missing_constraints: list[str]
    manual_review_required: bool
    policy_rag_enabled: bool = False
    policy_rag_available: bool = False
    policy_matched_rules: list[dict[str, Any]] = Field(default_factory=list)
    policy_sources: list[dict[str, Any]] = Field(default_factory=list)
    policy_assessment: str | None = None
    policy_compliance: Literal["符合", "有条件符合", "不符合", "依据不足"] | None = None
    policy_reason: str | None = None
    policy_requirements: list[str] = Field(default_factory=list)
    policy_warning: str | None = None


class AdaptationResult(BaseModel):
    block_id: str
    development_probability: float = Field(ge=0, le=100)
    suitability_score: float = Field(ge=0, le=100)
    planning_match: float = Field(ge=0, le=100)
    constraint_cost: float = Field(ge=0, le=100)
    dynamic_score: float = Field(ge=0, le=100)
    component_weights: dict[str, float]
    key_drivers: list[str]
    missing_components: list[str]
    recommended_use: str
    rank_city: int = Field(ge=1)
    rank_district: int = Field(ge=1)


DecisionType = Literal["一级优先调入", "二级优先调入", "建议调出", "内部更新", "保持现状", "人工复核"]


class BlockDecision(BaseModel):
    block_id: str
    block_name: str | None = None
    inside_boundary: bool
    boundary_overlap_ratio: float = Field(default=0, ge=0, le=1)
    decision: DecisionType
    overall_suitability: float
    development_probability: float = Field(default=0, ge=0, le=100)
    planning_match: float = Field(default=50, ge=0, le=100)
    constraint_cost: float = Field(default=0, ge=0, le=100)
    dynamic_score: float = Field(default=0, ge=0, le=100)
    confidence: float
    constraint_status: str
    key_reasons: list[str]
    risks: list[str]
    missing_information: list[str]
    manual_review_required: bool
    recommendation: str
    recommended_use: str = "待规划论证"
    key_drivers: list[str] = Field(default_factory=list)
    policy_matched_rules: list[dict[str, Any]] = Field(default_factory=list)
    policy_sources: list[dict[str, Any]] = Field(default_factory=list)
    policy_assessment: str | None = None
    policy_compliance: Literal["符合", "有条件符合", "不符合", "依据不足"] | None = None
    policy_reason: str | None = None
    policy_requirements: list[str] = Field(default_factory=list)
    rank_city: int | None = Field(default=None, ge=1)
    rank_district: int | None = Field(default=None, ge=1)
    rule_version: str
    ai_explanation: str | None = None


class ReviewResult(BaseModel):
    block_id: str
    status: Literal["pass", "revise", "human_review"]
    issues: list[str]


class TaskOutput(BaseModel):
    task_id: str
    project_version: str
    decisions: list[BlockDecision]
    reviews: list[ReviewResult]
    summary: dict[str, Any]


ChatAction = Literal[
    "show_data",
    "analyze_blocks",
    "find_inclusion",
    "find_exclusion",
    "compare_blocks",
    "summarize_all",
    "help",
]


class ChatIntent(BaseModel):
    """大模型或本地解析器输出的结构化用户意图。"""

    action: ChatAction = "analyze_blocks"
    block_ids: list[str] = Field(default_factory=list)
    block_names: list[str] = Field(default_factory=list)
    inside_boundary: bool | None = None
    requested_decisions: list[DecisionType] = Field(default_factory=list)
    analyze_all: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)
    clarification_needed: bool = False
    clarification_question: str | None = None
    parser: Literal["llm", "fallback"] = "fallback"


class ChatTurnResult(BaseModel):
    """一次对话请求的可保存结果。"""

    intent: ChatIntent
    response: str
    selected_block_ids: list[str] = Field(default_factory=list)
    block_records: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[BlockDecision] = Field(default_factory=list)
    workflow_summary: dict[str, Any] = Field(default_factory=dict)
    model_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
