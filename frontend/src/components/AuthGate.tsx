import { useState, type ReactNode } from "react";
import { googleLoginUrl, logout, useUser, type CurrentUser } from "../lib/auth";
import { Button } from "./ui/button";

type AuthGateProps = {
  children: (
    user: CurrentUser,
    signOut: () => Promise<void>,
    onUserUpdate: (next: CurrentUser) => void,
  ) => ReactNode;
};

function VoidShell({ children }: { children: ReactNode }) {
  return (
    <div className="cerno-void flex h-screen flex-col items-center justify-center gap-6 px-6">
      <div className="pointer-events-none fixed inset-0 cerno-grid opacity-40" />
      <div className="relative z-10 flex flex-col items-center gap-6">{children}</div>
    </div>
  );
}

function Wordmark() {
  return (
    <div className="flex flex-col items-center gap-2">
      <span className="font-display text-3xl font-medium tracking-[0.2em] text-foreground glow-iris">
        CERNO
      </span>
      <span className="font-hud text-[9px] text-iris/70">INTELLIGENCE SYSTEM</span>
    </div>
  );
}

export function AuthGate({ children }: AuthGateProps) {
  const { user: serverUser, loading, error, refresh } = useUser();
  const [override, setOverride] = useState<CurrentUser | null>(null);

  const user = override ?? serverUser;

  if (loading && !override) {
    return (
      <VoidShell>
        <span className="font-hud text-xs text-iris/70 animate-pulse">
          AUTHENTICATING…
        </span>
      </VoidShell>
    );
  }

  // A failed/timed-out probe (e.g. backend unreachable) should not strand the
  // user on a loading screen — let them sign in and retry.
  if (!user) {
    return <SignInScreen note={error ? "Could not reach the system. Try signing in." : undefined} />;
  }

  const signOut = async () => {
    setOverride(null);
    await logout();
    await refresh();
  };

  return <>{children(user, signOut, setOverride)}</>;
}

function SignInScreen({ note }: { note?: string }) {
  return (
    <VoidShell>
      <div className="relative flex w-full max-w-md flex-col items-center gap-8 border border-primary/20 bg-card px-10 py-12 box-glow-iris">
        <Wordmark />
        <p className="max-w-xs text-center text-sm leading-6 text-foreground/55">
          Link-aware data intelligence, operated under controlled access. Sign in
          to enter the system.
        </p>
        {note && (
          <p className="-mt-4 max-w-xs text-center font-mono text-[11px] text-alert">{note}</p>
        )}
        <Button asChild className="w-full">
          <a href={googleLoginUrl()}>CONTINUE WITH GOOGLE</a>
        </Button>
        <a
          href="/"
          className="font-hud text-[10px] text-foreground/45 transition-colors hover:text-iris"
        >
          ← BACK TO CERNO
        </a>
      </div>
    </VoidShell>
  );
}
