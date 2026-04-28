import { useEffect, useState } from "react";

export type CurrentUser = {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
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
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setUser(await fetchCurrentUser());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return { user, loading, error, refresh };
}
