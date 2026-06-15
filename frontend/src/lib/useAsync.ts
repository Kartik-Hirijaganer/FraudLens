/**
 * Summary: A small data-fetching hook the pages share so loading / error / retry behave
 * identically everywhere (rule 5: no duplication; plan §16 Phase 11 loading+retry). It
 * runs an async function, tracks `{data, loading, error}`, ignores a resolution after
 * unmount or after a newer run (no setState-after-unmount, no race), and exposes `reload`
 * to re-run on demand (the Retry action). Re-runs when the caller's `deps` change.
 *
 * Key classes:
 * - AsyncState: the hook's return shape (data + loading + error + reload).
 *
 * Key functions:
 * - useAsync: run an async function and track its state, with manual reload.
 *
 * Notes:
 * - `fn` is intentionally excluded from the effect deps (callers pass a fresh closure each
 *   render); `deps` is the explicit re-fetch trigger, plus an internal reload nonce.
 */
import { useCallback, useEffect, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: unknown;
  reload: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void fn().then(
      (result) => {
        if (active) {
          setData(result);
          setLoading(false);
        }
      },
      (caught: unknown) => {
        if (active) {
          setError(caught);
          setLoading(false);
        }
      },
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload };
}
