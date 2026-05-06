import { useState, type FormEvent, type ReactNode } from "react";
import { CernoLockup } from "./Brand";
import {
  RedeemError,
  logout,
  redeemBetaCode,
  type CurrentUser,
} from "../lib/auth";

type BetaGateProps = {
  user: CurrentUser;
  onUserUpdate: (user: CurrentUser) => void;
  children: ReactNode;
};

export function BetaGate({ user, onUserUpdate, children }: BetaGateProps) {
  if (user.access_status === "granted") {
    return <>{children}</>;
  }
  if (user.access_status === "revoked") {
    return <RevokedScreen email={user.email} />;
  }
  return <RedeemForm user={user} onUserUpdate={onUserUpdate} />;
}

function RedeemForm({
  user,
  onUserUpdate,
}: {
  user: CurrentUser;
  onUserUpdate: (user: CurrentUser) => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!code.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await redeemBetaCode(code.trim());
      onUserUpdate(updated);
    } catch (e) {
      if (e instanceof RedeemError) {
        setError(e.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onSignOut = async () => {
    await logout();
    window.location.reload();
  };

  return (
    <div className="flex h-screen items-center justify-center bg-tidepaper text-night-watch">
      <div className="w-full max-w-md border border-drift bg-drift/10 px-8 py-10 shadow-sm">
        <CernoLockup
          markClassName="h-7 w-7 text-deep-sea"
          wordmarkClassName="text-xl text-night-watch"
        />
        <h1 className="mt-6 font-serif text-xl font-medium">Beta access</h1>
        <p className="mt-2 text-sm text-night-watch/70">
          Cerno is in private beta. Enter the access code you were given to
          continue.
        </p>
        <p className="mt-1 text-xs text-night-watch/50">
          Signed in as <span className="text-night-watch">{user.email}</span>
        </p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-3">
          <label className="text-xs uppercase tracking-wide text-night-watch/50">
            Access code
          </label>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="CERNO-XXXX-XXXX"
            autoFocus
            spellCheck={false}
            autoCapitalize="characters"
            className="border border-drift bg-tidepaper px-3 py-2 font-mono text-sm uppercase tracking-wider text-night-watch outline-none focus:border-deep-sea"
          />
          {error ? (
            <p className="text-sm text-red-600">{error}</p>
          ) : null}
          <button
            type="submit"
            disabled={submitting || !code.trim()}
            className="mt-2 bg-night-watch px-4 py-2 text-sm font-medium text-tidepaper hover:bg-deep-sea disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Verifying…" : "Continue"}
          </button>
        </form>

        <button
          type="button"
          onClick={onSignOut}
          className="mt-6 text-xs text-night-watch/50 hover:text-night-watch"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

function RevokedScreen({ email }: { email: string }) {
  const onSignOut = async () => {
    await logout();
    window.location.reload();
  };
  return (
    <div className="flex h-screen items-center justify-center bg-tidepaper text-night-watch">
      <div className="w-full max-w-md border border-red-900/20 bg-red-50 px-8 py-10 shadow-sm">
        <CernoLockup
          markClassName="h-7 w-7 text-red-600"
          wordmarkClassName="text-xl text-red-800"
        />
        <h1 className="mt-6 font-serif text-xl font-medium text-red-800">Access revoked</h1>
        <p className="mt-2 text-sm text-red-900/70">
          Your access to Cerno has been revoked. If you think this is a
          mistake, contact the operator.
        </p>
        <p className="mt-1 text-xs text-red-900/50">
          Signed in as <span className="text-red-800">{email}</span>
        </p>
        <button
          type="button"
          onClick={onSignOut}
          className="mt-6 border border-red-900/20 px-4 py-2 text-sm font-medium text-red-800 hover:bg-red-100"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
