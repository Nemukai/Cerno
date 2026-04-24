import { useEffect, useRef, useState } from "react";

export type FetchState<T> = {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
};

export function useFetch<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    setLoading(true);
    setError(null);
    fn()
      .then((result) => {
        if (cancelled.current) return;
        setData(result);
      })
      .catch((err: Error) => {
        if (cancelled.current) return;
        setError(err);
      })
      .finally(() => {
        if (cancelled.current) return;
        setLoading(false);
      });
    return () => {
      cancelled.current = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadKey]);

  return {
    data,
    error,
    loading,
    reload: () => setReloadKey((k) => k + 1),
  };
}
