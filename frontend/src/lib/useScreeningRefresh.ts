/**
 * Summary: Read-only, non-overlapping progress polling shared by screening-aware pages.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - useScreeningRefresh: refresh persisted page data while automatic screening is active.
 *
 * Notes:
 * - Focus rechecks persisted work; mounting or signing in never creates investigations.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiClient, ScreeningProgress } from "./api";
import { useSession } from "./session";

export function useScreeningRefresh(client: ApiClient, onRefresh: () => void) {
  const [progress, setProgress] = useState<ScreeningProgress | null>(null);
  const [error, setError] = useState(false);
  const callback = useRef(onRefresh);
  const check = useRef<() => void>(() => undefined);
  const agencyId = useSession()?.agencyId;
  useEffect(() => {
    callback.current = onRefresh;
  }, [onRefresh]);
  useEffect(() => {
    let closed = false;
    let inFlight = false;
    let refreshQueued = false;
    let previous: ScreeningProgress | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setProgress(null);
    setError(false);
    const active = (value: ScreeningProgress | null) =>
      value ? value.pending + value.running + value.retrying : 0;
    async function poll(forceRefresh = false) {
      if (closed) return;
      if (inFlight) {
        refreshQueued ||= forceRefresh;
        return;
      }
      clearTimeout(timer);
      inFlight = true;
      try {
        const next = await client.getScreeningProgress();
        if (closed) return;
        setProgress(next);
        setError(false);
        if (
          forceRefresh ||
          active(previous) > 0 ||
          active(next) > 0 ||
          (previous && next.total !== previous.total)
        )
          callback.current();
        previous = next;
      } catch {
        if (!closed) setError(true);
      } finally {
        inFlight = false;
        if (!closed && refreshQueued) {
          refreshQueued = false;
          void poll(true);
        } else if (!closed && active(previous) > 0 && previous) {
          timer = setTimeout(() => void poll(), previous.pollAfterMs);
        }
      }
    }
    const recheck = () => {
      void poll(true);
    };
    check.current = recheck;
    window.addEventListener("focus", recheck);
    void poll();
    return () => {
      closed = true;
      clearTimeout(timer);
      window.removeEventListener("focus", recheck);
    };
  }, [client, agencyId]);
  const refresh = useCallback(() => check.current(), []);
  return { progress, error, refresh };
}
