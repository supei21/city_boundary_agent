import type {
  ArtifactDetail,
  ArtifactSummary,
  CreateUserModelResponse,
  DatasetPayload,
  StreamPacket,
  UserModelInput,
  WorkspaceSummary,
} from "./types";

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

export function generatePolicyReport(modelId: string): Promise<ArtifactSummary> {
  return request("/reports/policy-pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
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
