import { useQuery, useQueryClient } from "@tanstack/react-query";

export type AccessStatus = "pending" | "granted" | "revoked";
export type OrganizationRole = "owner" | "admin" | "member" | "viewer";

export type CurrentUser = {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  access_status: AccessStatus;
  site_role: "user" | "site_owner";
  organizations: Array<{
    id: string;
    name: string;
    slug: string;
    role: OrganizationRole;
  }>;
};

const AUTH_BASE = "/api/auth";

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  const res = await fetch(`${AUTH_BASE}/me`, { credentials: "same-origin" });
  if (res.status === 401) return null;
  if (!res.ok) {
    throw new Error(`auth/me failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as CurrentUser;
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
