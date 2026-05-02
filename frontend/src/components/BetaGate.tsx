import { useState, type FormEvent, type ReactNode } from "react";
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
    <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="w-full max-w-md rounded-lg border border-neutral-800 bg-neutral-900 px-8 py-10 shadow-xl">
        <h1 className="text-xl font-semibold">Beta access</h1>
        <p className="mt-2 text-sm text-neutral-400">
          Cerno is in private beta. Enter the access code you were given to
          continue.
        </p>
        <p className="mt-1 text-xs text-neutral-500">
          Signed in as <span className="text-neutral-300">{user.email}</span>
        </p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-3">
          <label className="text-xs uppercase tracking-wide text-neutral-500">
            Access code
          </label>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="CERNO-XXXX-XXXX"
            autoFocus
            spellCheck={false}
            autoCapitalize="characters"
            className="rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 font-mono text-sm uppercase tracking-wider text-neutral-100 outline-none focus:border-neutral-500"
          />
          {error ? (
            <p className="text-sm text-red-400">{error}</p>
          ) : null}
          <button
            type="submit"
            disabled={submitting || !code.trim()}
            className="mt-2 rounded-md bg-white px-4 py-2 text-sm font-medium text-neutral-900 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Verifying…" : "Continue"}
          </button>
        </form>

        <button
          type="button"
          onClick={onSignOut}
          className="mt-6 text-xs text-neutral-500 hover:text-neutral-300"
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
    <div className="flex h-screen items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="w-full max-w-md rounded-lg border border-red-900/40 bg-neutral-900 px-8 py-10 shadow-xl">
        <h1 className="text-xl font-semibold text-red-400">Access revoked</h1>
        <p className="mt-2 text-sm text-neutral-400">
          Your access to Cerno has been revoked. If you think this is a
          mistake, contact the operator.
        </p>
        <p className="mt-1 text-xs text-neutral-500">
          Signed in as <span className="text-neutral-300">{email}</span>
        </p>
        <button
          type="button"
          onClick={onSignOut}
          className="mt-6 rounded-md border border-neutral-700 px-4 py-2 text-sm font-medium text-neutral-200 hover:bg-neutral-800"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
