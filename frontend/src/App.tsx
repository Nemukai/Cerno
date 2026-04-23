import { useEffect, useState } from "react";
import { getHealth, type Health } from "./lib/api";

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">Cerno</h1>
      <p className="text-sm text-neutral-400">Discern what matters.</p>
      <div className="mt-8 rounded-md border border-neutral-800 bg-neutral-900 px-4 py-3 font-mono text-xs text-neutral-400">
        {error ? (
          <span className="text-red-400">backend: {error}</span>
        ) : health ? (
          <span>
            backend ok · v{health.version}
          </span>
        ) : (
          <span>connecting…</span>
        )}
      </div>
    </div>
  );
}
