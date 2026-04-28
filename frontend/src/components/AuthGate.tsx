import type { ReactNode } from "react";
import { googleLoginUrl, logout, useUser, type CurrentUser } from "../lib/auth";

type AuthGateProps = {
  children: (user: CurrentUser, signOut: () => Promise<void>) => ReactNode;
};

export function AuthGate({ children }: AuthGateProps) {
  const { user, loading, error, refresh } = useUser();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-400">
        Loading…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-neutral-950 text-red-400">
        Failed to load session: {error}
      </div>
    );
  }

  if (!user) {
    return <SignInScreen />;
  }

  const signOut = async () => {
    await logout();
    await refresh();
  };

  return <>{children(user, signOut)}</>;
}

function SignInScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="flex flex-col items-center gap-6 rounded-lg border border-neutral-800 bg-neutral-900 px-10 py-12 shadow-xl">
        <div>
          <h1 className="text-2xl font-semibold">Cerno</h1>
          <p className="mt-1 text-sm text-neutral-400">
            Link-aware data analysis. Sign in to begin.
          </p>
        </div>
        <a
          href={googleLoginUrl()}
          className="rounded-md bg-white px-4 py-2 text-sm font-medium text-neutral-900 hover:bg-neutral-100"
        >
          Continue with Google
        </a>
      </div>
    </div>
  );
}
