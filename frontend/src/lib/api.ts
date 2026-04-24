import type {
  BuildDashboardResponse,
  ChatResponse,
  ChatMessage,
  ChatTurn,
  DashboardResponse,
  FileRecord,
  FileSchemaResponse,
  Link,
  LinkAction,
  LinkReview,
  Session,
  Anomaly,
} from "./types";

export type Health = {
  status: string;
  version: string;
};

const API_BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${body}`.trim());
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function postMultipart<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: form });
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function createSession(name: string): Promise<Session> {
  return postJson<Session>("/sessions", { name });
}

export function listSessions(): Promise<Session[]> {
  return request<Session[]>("/sessions");
}

export function getSession(id: string): Promise<Session> {
  return request<Session>(`/sessions/${id}`);
}

export function uploadFiles(
  sessionId: string,
  files: File[],
): Promise<{ files: FileRecord[] }> {
  const form = new FormData();
  for (const f of files) form.append("uploads", f, f.name);
  return postMultipart<{ files: FileRecord[] }>(
    `/sessions/${sessionId}/files`,
    form,
  );
}

export function listFiles(sessionId: string): Promise<FileRecord[]> {
  return request<FileRecord[]>(`/sessions/${sessionId}/files`);
}

export function getFileSchema(fileId: string): Promise<FileSchemaResponse> {
  return request<FileSchemaResponse>(`/files/${fileId}/schema`);
}

export function discoverLinks(sessionId: string): Promise<Link[]> {
  return postJson<Link[]>(`/sessions/${sessionId}/discover-links`, {});
}

export function listLinks(sessionId: string): Promise<Link[]> {
  return request<Link[]>(`/sessions/${sessionId}/links`);
}

export function reviewLink(
  linkId: string,
  action: LinkAction,
  notes?: string,
): Promise<LinkReview> {
  return postJson<LinkReview>(`/links/${linkId}/review`, { action, notes });
}

export function skipReview(
  sessionId: string,
): Promise<{ auto_confirmed_count: number }> {
  return postJson<{ auto_confirmed_count: number }>(
    `/sessions/${sessionId}/skip-review`,
    {},
  );
}

export function buildDashboard(
  sessionId: string,
): Promise<BuildDashboardResponse> {
  return postJson<BuildDashboardResponse>(
    `/sessions/${sessionId}/build-dashboard`,
    {},
  );
}

export function getDashboard(sessionId: string): Promise<DashboardResponse> {
  return request<DashboardResponse>(`/sessions/${sessionId}/dashboard`);
}

export function getAnomalies(
  sessionId: string,
  limit = 20,
): Promise<Anomaly[]> {
  return request<Anomaly[]>(`/sessions/${sessionId}/anomalies?limit=${limit}`);
}

export function postChat(
  sessionId: string,
  message: string,
): Promise<ChatResponse> {
  return postJson<ChatResponse>(`/sessions/${sessionId}/chat`, { message });
}

export function listTurns(sessionId: string): Promise<ChatTurn[]> {
  return request<ChatTurn[]>(`/sessions/${sessionId}/turns`);
}

export function listTurnMessages(turnId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/turns/${turnId}/messages`);
}
