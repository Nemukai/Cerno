import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { NumberSystem } from "./format-number";

export type { NumberSystem } from "./format-number";

export type AccessStatus = "pending" | "granted" | "revoked";
export type OrganizationRole = "admin" | "member" | "viewer";

export type CurrentUser = {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  access_status: AccessStatus;
  site_role: "user" | "site_owner";
  number_system: NumberSystem;
  organizations: Array<{
    id: string;
    name: string;
    slug: string;
    role: OrganizationRole;
  }>;
};

const AUTH_BASE = "/api/auth";

async function fetchCurrentUser(): Promise<CurrentUser | null> {
  // Bound the probe so a slow/unreachable backend can never hang the UI.
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(`${AUTH_BASE}/me`, {
      credentials: "same-origin",
      signal: controller.signal,
    });
    if (res.status === 401) return null;
    if (!res.ok) {
      throw new Error(`auth/me failed: ${res.status} ${res.statusText}`);
    }
    return (await res.json()) as CurrentUser;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function googleLoginUrl(): string {
  return `${AUTH_BASE}/google/login`;
}

export async function logout(): Promise<void> {
  await fetch(`${AUTH_BASE}/logout`, {
    method: "POST",
    credentials: "same-origin",
  });
}

export async function updateUserSettings(input: {
  number_system: NumberSystem;
}): Promise<CurrentUser> {
  const res = await fetch(`${AUTH_BASE}/me/settings`, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${body}`.trim());
  }
  return (await res.json()) as CurrentUser;
}

export type UseUser = {
  user: CurrentUser | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export function useUser(): UseUser {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: 1,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    await query.refetch();
  };

  return {
    user: query.data ?? null,
    loading: query.isPending,
    error: query.error ? (query.error as Error).message : null,
    refresh,
  };
}
