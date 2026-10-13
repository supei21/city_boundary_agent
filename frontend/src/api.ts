import type {
  ArtifactDetail,
  ArtifactSummary,
  CreateUserModelResponse,
  DatasetPayload,
  LandscapeEvaluation,
  LandscapeProgress,
  LandscapeScenarioEvaluation,
  RagPolicyDocumentDetail,
  RagPolicyDocumentSummary,
  RagPolicySearchResult,
  StreamPacket,
  UserModelInput,
  WorkspaceSummary,
} from "./types";
import type { FeatureCollection } from "geojson";

const API_ROOT = import.meta.env.VITE_API_URL ?? `${window.location.origin}/api`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function getWorkspace(): Promise<WorkspaceSummary> {
  return request("/workspace");
}

export function getDataset(offset = 0, limit = 50, q = ""): Promise<DatasetPayload> {
  const query = q.trim() ? `&q=${encodeURIComponent(q.trim())}` : "";
  return request(`/dataset?offset=${offset}&limit=${limit}${query}`);
}

export function resetWorkspace(): Promise<WorkspaceSummary> {
  return request("/workspace/reset", { method: "POST" });
}

export function uploadDataset(file: File): Promise<WorkspaceSummary> {
  const form = new FormData();
  form.append("file", file);
  return request("/datasets/upload", { method: "POST", body: form });
}

export function uploadConstraintDataset(file: File): Promise<WorkspaceSummary> {
  const form = new FormData();
  form.append("file", file);
  return request("/constraints/upload", { method: "POST", body: form });
}

export function getLandscapeBoundaryGeoJson(): Promise<FeatureCollection> {
  return request("/landscape/boundary/geojson");
}

export function evaluateLandscape(input: {
  block_ids?: string[];
  block_actions?: Record<string, "include" | "exclude">;
  action?: "include" | "exclude" | "auto";
  limit?: number;
}): Promise<LandscapeEvaluation> {
  return request("/landscape/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function streamLandscapeEvaluation(
  input: {
    block_ids?: string[];
    block_actions?: Record<string, "include" | "exclude">;
    action?: "include" | "exclude" | "auto";
    limit?: number;
  },
  onProgress: (progress: LandscapeProgress) => void,
): Promise<LandscapeEvaluation> {
  const response = await fetch(`${API_ROOT}/landscape/evaluate/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `无法启动形态评价（${response.status}）`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: LandscapeEvaluation | null = null;

  const handleLine = (line: string) => {
    if (!line.trim()) return;
    const packet = JSON.parse(line) as
      | { type: "progress"; progress: LandscapeProgress }
      | { type: "result"; result: LandscapeEvaluation }
      | { type: "error"; message: string };
    if (packet.type === "progress") onProgress(packet.progress);
    if (packet.type === "result") result = packet.result;
    if (packet.type === "error") throw new Error(packet.message);
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) handleLine(line);
    if (done) break;
  }
  if (buffer.trim()) handleLine(buffer);
  if (!result) throw new Error("形态评价结束，但后端未返回结果");
  return result;
}

export function evaluateLandscapeScenario(input: {
  block_ids: string[];
  action: "include" | "exclude";
}): Promise<LandscapeScenarioEvaluation> {
  return request("/landscape/scenario/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function generatePolicyReport(modelId: string): Promise<ArtifactSummary> {
  return request("/reports/policy-pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
}

export function listRagPolicies(): Promise<RagPolicyDocumentSummary[]> {
  return request("/rag/policies");
}

export function getRagPolicyDocument(id: string): Promise<RagPolicyDocumentDetail> {
  return request(`/rag/policies/${encodeURIComponent(id)}`);
}

export function searchRagPolicies(query: string, limit = 8): Promise<RagPolicySearchResult[]> {
  return request(`/rag/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export function getArtifact(id: string): Promise<ArtifactDetail> {
  return request(`/artifacts/${id}`);
}

export function artifactPreviewUrl(id: string): string {
  return `${API_ROOT}/artifacts/${id}/preview`;
}

export function artifactDownloadUrl(id: string): string {
  return `${API_ROOT}/artifacts/${id}/download`;
}

export function policyReferencePdfUrl(): string {
  return `${API_ROOT}/policies/reference-pdf/preview`;
}

export function createUserModel(input: UserModelInput): Promise<CreateUserModelResponse> {
  return request("/models", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function streamChat(
  query: string,
  modelId: string,
  onPacket: (packet: StreamPacket) => void,
): Promise<void> {
  const response = await fetch(`${API_ROOT}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, model_id: modelId }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`无法启动分析（${response.status}）`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) onPacket(JSON.parse(line) as StreamPacket);
    }
    if (done) break;
  }
  if (buffer.trim()) onPacket(JSON.parse(buffer) as StreamPacket);
}
