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
      <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-400">
        Loading…
      </div>
    );
  }

  if (error && !override) {
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
    setOverride(null);
    await logout();
    await refresh();
  };

  return <>{children(user, signOut, setOverride)}</>;
}

function SignInScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="flex flex-col items-center gap-6 rounded-lg border border-neutral-800 bg-neutral-900 px-10 py-12 shadow-xl">
        <div className="text-center">
          <CernoLockup
            className="justify-center"
            markClassName="h-8 w-8"
            wordmarkClassName="text-2xl text-neutral-100"
          />
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
