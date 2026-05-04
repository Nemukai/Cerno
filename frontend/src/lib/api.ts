import type {
  Anomaly,
  BuildDashboardResponse,
  ChatMessage,
  ChatResponse,
  ChatTurn,
  DataDoc,
  DashboardResponse,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FilePreviewResponse,
  FileRecord,
  Link,
  ProcessingEvent,
  Session,
} from "./types";

export type Health = {
  status: string;
  version: string;
};

const API_BASE = "/api";

export class UnauthorizedError extends Error {
  constructor() {
    super("unauthorized");
    this.name = "UnauthorizedError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    ...init,
  });
  if (res.status === 401) {
    throw new UnauthorizedError();
  }
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

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${body}`.trim());
  }
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

export async function deleteFile(fileId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/files/${fileId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${body}`.trim());
  }
}

export function getFilePreview(
  fileId: string,
  limit = 100,
): Promise<FilePreviewResponse> {
  return request<FilePreviewResponse>(
    `/files/${fileId}/preview?limit=${limit}`,
  );
}

export function processSession(sessionId: string): Promise<DiscoveryResponse> {
  return postJson<DiscoveryResponse>(`/sessions/${sessionId}/process`, {});
}

export function getDiscovery(sessionId: string): Promise<DiscoveryResponse> {
  return request<DiscoveryResponse>(`/sessions/${sessionId}/discovery`);
}

export function approveSchema(
  sessionId: string,
  body: {
    files: DiscoveredFile[];
    links: DiscoveredLink[];
    overview: string;
  },
): Promise<DiscoveryResponse> {
  return postJson<DiscoveryResponse>(
    `/sessions/${sessionId}/approve-schema`,
    body,
  );
}

export function getProcessingEvents(
  sessionId: string,
): Promise<ProcessingEvent[]> {
  return request<ProcessingEvent[]>(`/sessions/${sessionId}/processing`);
}

export function listLinks(sessionId: string): Promise<Link[]> {
  return request<Link[]>(`/sessions/${sessionId}/links`);
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

export function getDataDocs(sessionId: string): Promise<DataDoc> {
  return request<DataDoc>(`/sessions/${sessionId}/docs`);
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
