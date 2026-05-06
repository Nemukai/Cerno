import { useState, type ReactNode } from "react";
import { CernoLockup } from "./Brand";
import { googleLoginUrl, logout, useUser, type CurrentUser } from "../lib/auth";

type AuthGateProps = {
  children: (
    user: CurrentUser,
    signOut: () => Promise<void>,
    onUserUpdate: (next: CurrentUser) => void,
  ) => ReactNode;
};

export function AuthGate({ children }: AuthGateProps) {
  const { user: serverUser, loading, error, refresh } = useUser();
  const [override, setOverride] = useState<CurrentUser | null>(null);

  const user = override ?? serverUser;

  if (loading && !override) {
    return (
      <div className="flex h-screen items-center justify-center bg-tidepaper text-night-watch/60">
        Loading…
      </div>
    );
  }

  if (error && !override) {
    return (
      <div className="flex h-screen items-center justify-center bg-tidepaper text-red-600">
        Failed to load session: {error}
      </div>
    );
  }

  if (!user) {
    return <SignInScreen />;
  }

  const signOut = async () => {
    setOverride(null);
    await logout();
    await refresh();
  };

  return <>{children(user, signOut, setOverride)}</>;
}

function SignInScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-tidepaper text-night-watch">
      <div className="flex flex-col items-center gap-6 border border-drift bg-drift/10 px-10 py-12 shadow-sm">
        <div className="text-center">
          <CernoLockup
            className="justify-center"
            markClassName="h-8 w-8 text-deep-sea"
            wordmarkClassName="text-2xl text-night-watch"
          />
          <p className="mt-1 text-sm text-night-watch/70">
            Link-aware data analysis. Sign in to begin.
          </p>
        </div>
        <a
          href={googleLoginUrl()}
          className="bg-night-watch px-4 py-2 text-sm font-medium text-tidepaper hover:bg-deep-sea"
        >
          Continue with Google
        </a>
      </div>
    </div>
  );
}
