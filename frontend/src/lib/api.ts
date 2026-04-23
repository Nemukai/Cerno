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

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}
