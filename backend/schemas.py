from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(default="default", min_length=1, max_length=80)


class ModelOption(BaseModel):
    id: str
    label: str
    description: str
    ready: bool
    kind: Literal["local", "llm"]
    base_url: str = ""


class ArtifactSummary(BaseModel):
    id: str
    name: str
    kind: Literal["dataset", "analysis", "spatial", "geojson", "report", "pdf"]
    mime: str
    size: int
    created_at: str


class ArtifactDetail(ArtifactSummary):
    preview: Any = None


class ConstraintDatasetSummary(BaseModel):
    loaded: bool = False
    name: str | None = None
    layer_count: int = 0
    layers: list[str] = Field(default_factory=list)
    uploaded_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PolicyDocumentSummary(BaseModel):
    id: str
    name: str
    size: int
    uploaded_at: str
    text_length: int = 0
    excerpt: str = ""
    warning: str | None = None


class WorkspaceSummary(BaseModel):
    dataset_name: str
    block_count: int
    field_count: int
    has_spatial_data: bool = False
    dataset_warnings: list[str] = Field(default_factory=list)
    constraint_dataset: ConstraintDatasetSummary = Field(default_factory=ConstraintDatasetSummary)
    policy_documents: list[PolicyDocumentSummary] = Field(default_factory=list)
    model_diagnostics: dict[str, Any] = Field(default_factory=dict)
    model_ready: bool
    default_model_id: str
    models: list[ModelOption] = Field(default_factory=list)
    artifacts: list[ArtifactSummary] = Field(default_factory=list)


class DatasetPayload(BaseModel):
    name: str
    warnings: list[str] = Field(default_factory=list)
    block_count: int
    matched_count: int
    offset: int = 0
    limit: int = 50
    has_more: bool = False
    blocks: list[dict[str, Any]]


class UserModelInput(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=300)

    @field_validator("label", "model", "base_url", "api_key")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def _http_scheme(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("网址必须以 http:// 或 https:// 开头")
        return value


class UserModelOutput(BaseModel):
    """回传给前端的模型信息，绝不包含完整 api_key。"""

    id: str
    label: str
    model: str
    base_url: str
    created_at: str
    api_key_masked: str


class CreateUserModelResponse(BaseModel):
    model: UserModelOutput
    workspace: WorkspaceSummary


class ReportGenerateRequest(BaseModel):
    model_id: str = Field(default="default", min_length=1, max_length=80)
