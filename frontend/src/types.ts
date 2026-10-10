export interface ArtifactSummary {
  id: string;
  name: string;
  kind: "dataset" | "analysis" | "spatial" | "geojson" | "report" | "pdf";
  mime: string;
  size: number;
  created_at: string;
}

export interface ArtifactDetail extends ArtifactSummary {
  preview: unknown;
}

export interface ModelOption {
  id: string;
  label: string;
  description: string;
  ready: boolean;
  kind: "local" | "llm";
  base_url?: string;
}

export interface ConstraintDatasetSummary {
  loaded: boolean;
  name?: string | null;
  layer_count: number;
  layers: string[];
  uploaded_at?: string | null;
  warnings: string[];
}

export interface PolicyDocumentSummary {
  id: string;
  name: string;
  size: number;
  uploaded_at: string;
  text_length: number;
  excerpt: string;
  warning?: string | null;
  builtin?: boolean;
}

export interface WorkspaceSummary {
  dataset_name: string;
  block_count: number;
  field_count: number;
  has_spatial_data: boolean;
  dataset_warnings: string[];
  constraint_dataset: ConstraintDatasetSummary;
  policy_documents: PolicyDocumentSummary[];
  model_diagnostics: {
    enabled?: boolean;
    available?: boolean;
    model_dir?: string;
    feature_count?: number;
    metrics?: Record<string, number | boolean | string | number[] | Record<string, unknown> | null>;
    shap_available?: boolean;
    shap_top?: { feature: string; mean_abs_shap: number }[];
    warning?: string;
  };
  model_ready: boolean;
  default_model_id: string;
  models: ModelOption[];
  artifacts: ArtifactSummary[];
}

export type MainTabId = "chat" | "data" | "map" | "policy";

export interface DatasetPayload {
  name: string;
  warnings: string[];
  block_count: number;
  matched_count: number;
  offset: number;
  limit: number;
  has_more: boolean;
  blocks: Record<string, unknown>[];
}

export interface AgentEvent {
  phase: "understand" | "action" | "observe" | "review" | "answer" | "error";
  title: string;
  detail?: string | null;
  status: "running" | "completed" | "warning" | "error";
  metadata: Record<string, unknown>;
}

export interface Decision {
  block_id: string;
  block_name?: string | null;
  decision: string;
  overall_suitability: number;
  development_probability: number;
  planning_match: number;
  constraint_cost: number;
  dynamic_score: number;
  confidence: number;
  recommended_use: string;
  key_drivers: string[];
  rank_city?: number | null;
  rank_district?: number | null;
  manual_review_required: boolean;
  policy_compliance?: "符合" | "有条件符合" | "不符合" | "依据不足" | null;
  policy_reason?: string | null;
  policy_requirements?: string[];
}

export interface ChatResult {
  response: string;
  block_records: Record<string, unknown>[];
  decisions: Decision[];
  warnings: string[];
  model_used: boolean;
  model_id?: string;
  model_name?: string;
  workflow_summary: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  events?: AgentEvent[];
  result?: ChatResult;
  pending?: boolean;
  error?: boolean;
}

export type StreamPacket =
  | { type: "process"; event: AgentEvent }
  | { type: "answer_delta"; content: string }
  | { type: "result"; result: ChatResult; artifact: ArtifactSummary | null }
  | { type: "error"; message: string };

export interface UserModelInput {
  label: string;
  model: string;
  base_url: string;
  api_key: string;
}

export interface UserModelOutput {
  id: string;
  label: string;
  model: string;
  base_url: string;
  created_at: string;
  api_key_masked: string;
}

export interface CreateUserModelResponse {
  model: UserModelOutput;
  workspace: WorkspaceSummary;
}

export interface RagPolicyDocumentSummary {
  id: string;
  name: string;
  source: string;
  category: string;
  suffix: string;
  size: number;
  text_length: number;
  excerpt: string;
}

export interface RagPolicyDocumentDetail extends RagPolicyDocumentSummary {
  content: string;
}

export interface RagPolicySearchResult {
  document_id?: string | null;
  source: string;
  type: string;
  score: number;
  rule_id?: string | null;
  article_ref?: string | null;
  excerpt: string;
}
