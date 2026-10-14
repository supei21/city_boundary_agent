import type { FeatureCollection } from "geojson";

export interface ArtifactSummary {
  id: string;
  name: string;
  kind: "dataset" | "analysis" | "landscape" | "spatial" | "geojson" | "report" | "pdf";
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

export interface LandscapeBoundarySummary {
  loaded: boolean;
  name?: string | null;
  source_crs?: string | null;
  analysis_crs?: string | null;
  source_geometry_type?: string | null;
  source_feature_count: number;
  patch_count: number;
  area_km2: number;
  warnings: string[];
}

export type LandscapeGrade =
  | "significantly_improved"
  | "slightly_improved"
  | "stable"
  | "slightly_worsened"
  | "significantly_worsened";

export interface LandscapeResult {
  block_id: string;
  block_name?: string | null;
  action: "include" | "exclude";
  action_label: string;
  boundary_overlap_ratio: number;
  changed_area_km2: number;
  demo_shape_score: number;
  improved_metrics: string[];
  worsened_metrics: string[];
  metric_assessments: Record<string, {
    state: "improved" | "unchanged" | "worsened";
    strength: "significant" | "slight" | "stable";
    score: number;
    relative_change?: number | null;
    absolute_change?: number;
    threshold_basis?: "count" | "relative" | "percentage_point";
  }>;
  dimensions: Record<string, {
    label: string;
    metrics: string[];
    score: number;
    grade: LandscapeGrade;
    grade_label: string;
  }>;
  topology_check: {
    status: "no_change" | "island" | "nearby_connected" | "weak_connection" | "connected" | "split" | "intact";
    label: string;
    risk: boolean;
    before_patch_count: number;
    after_patch_count: number;
    shared_boundary_length_m: number;
    nearest_distance_m?: number;
    adjacency_tolerance_m?: number;
    message: string;
    risk_level: "none" | "low" | "medium" | "high";
    risk_label: string;
    affected_area_km2: number;
    affected_share_percent: number;
  };
  expansion_pattern?: {
    index: number;
    pattern: "infill" | "edge_expansion" | "outlying";
    label: string;
    buffer_m: number;
    occupied_buffer_area_m2: number;
    vacant_buffer_area_m2: number;
  } | null;
  overall_grade: LandscapeGrade;
  overall_label: string;
  overall_score: number;
  before: Record<string, number>;
  after: Record<string, number>;
  delta: Record<string, number>;
  conclusion: string;
}

export interface LandscapeEvaluation {
  type: "landscape_analysis";
  boundary: LandscapeBoundarySummary;
  spatial: FeatureCollection;
  summary: {
    evaluated_count: number;
    improved_count: number;
    worsened_count: number;
    balanced_count: number;
    grade_counts: Record<LandscapeGrade, number>;
    average_demo_shape_score: number;
    note: string;
  };
  results: LandscapeResult[];
  artifact: ArtifactSummary;
}

export interface LandscapeProgress {
  phase: "preparing" | "evaluating" | "finalizing" | "completed" | "error";
  status: "running" | "completed" | "error";
  completed: number;
  total: number;
  evaluated_count: number;
  percent: number;
  current_block_id?: string | null;
  message: string;
}

export interface PolicyFeasibilityProgress {
  phase: "preparing" | "retrieving" | "analyzing" | "completed" | "error";
  status: "running" | "completed" | "error";
  processed: number;
  completed: number;
  failed: number;
  total: number;
  percent: number;
  current_batch?: number | null;
  total_batches?: number | null;
  message: string;
}

export interface LandscapeScenarioConnection {
  block_id: string;
  block_name?: string | null;
  status: "direct" | "conditional" | "isolated" | "removal_member";
  label: string;
  group_id: string;
  connected_to_boundary: boolean;
  neighbor_block_ids: string[];
}

export type LandscapeScenarioResult = Omit<LandscapeResult, "block_id" | "block_name" | "action_label" | "boundary_overlap_ratio"> & {
  evaluation_mode: "scenario";
  selected_block_ids: string[];
  selected_count: number;
  action_label: string;
  block_connections: LandscapeScenarioConnection[];
  connection_counts: Record<"direct" | "conditional" | "isolated" | "removal_member", number>;
  groups: Array<{
    group_id: string;
    block_ids: string[];
    block_count: number;
    connected_to_boundary: boolean;
  }>;
};

export interface LandscapeScenarioEvaluation {
  type: "landscape_scenario_analysis";
  boundary: LandscapeBoundarySummary;
  summary: {
    selected_count: number;
    action: "include" | "exclude";
    action_label: string;
    group_count: number;
    connection_counts: Record<string, number>;
    overall_grade: LandscapeGrade;
    overall_label: string;
    note: string;
  };
  scenario: LandscapeScenarioResult;
  spatial: FeatureCollection;
  artifact: ArtifactSummary;
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
  landscape_boundary: LandscapeBoundarySummary;
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

export type MainTabId = "chat" | "data" | "map" | "policy" | "policy_qa" | "policy_feasibility";

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

export interface CandidateParcelPayload {
  available: boolean;
  analysis_required: boolean;
  count: number;
  parcels: Record<string, unknown>[];
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
  landscape_evaluation?: LandscapeEvaluation | null;
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
  | { type: "landscape_progress"; progress: LandscapeProgress }
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

export interface RagQuestionResponse {
  answer: string;
  mode: string;
  best_score: number;
  sources: RagPolicySearchResult[];
}

export interface PolicyFeasibilityResponse {
  tool_name: string;
  tool_version: string;
  parcel_id: string;
  policy_analysis: Record<string, unknown>;
  hard_constraint_analysis: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  parcel_context: {
    block_id?: string;
    block_name?: string | null;
    groups?: Record<string, { field: string; label: string; value: unknown; unit: string }[]>;
    available_field_count?: number;
    text?: string;
  };
  answer: string;
}

export interface PolicyBatchValidationResponse {
  tool_name: string;
  tool_version: string;
  total: number;
  passed: number;
  pass_rate: number;
  gate_match_rate: number | null;
  evidence_valid_rate: number;
  input_valid_rate: number;
  results: Record<string, unknown>[];
  report: string;
}

export interface CandidatePolicyAnalysisResponse {
  total: number;
  completed: number;
  failed: number;
  results: Record<string, unknown>[];
  decisions: Decision[];
  report: string;
  spatial: FeatureCollection | null;
  artifact: ArtifactSummary | null;
}
