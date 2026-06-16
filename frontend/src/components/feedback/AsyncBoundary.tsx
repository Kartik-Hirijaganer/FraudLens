/**
 * Summary: Renders the right feedback for an `useAsync` state so every page handles
 * loading / error / retry the same way (rule 5: no duplication). While the first load is
 * in flight it shows skeletons; on a first-load failure it shows an `ErrorState` with a
 * Retry wired to `reload` (the error copy comes from `describeError`, so it is PHI-free);
 * once data exists it renders it (and keeps showing it during a background reload).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AsyncBoundary: render skeleton / error+retry / data for an async state.
 *
 * Notes:
 * - During a reload that already has data, the stale data stays on screen (no skeleton
 * flash); a reload error is left for the caller to surface (e.g. as a toast).
 */
import type { ReactNode } from "react";

import { describeError } from "../../lib/errors";
import type { AsyncState } from "../../lib/useAsync";
import { ErrorState } from "./ErrorState";
import { Skeleton } from "./Skeleton";

interface AsyncBoundaryProps<T> {
  state: AsyncState<T>;
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
}

export function AsyncBoundary<T>({ state, skeleton, children }: AsyncBoundaryProps<T>) {
  if (state.data === null && state.loading) {
    return (
      <>
        {skeleton ?? (
          <div className="gap-md flex flex-col">
            <Skeleton className="h-2xl w-1/3" />
            <Skeleton className="h-3xl w-full" />
            <Skeleton className="h-3xl w-full" />
          </div>
        )}
      </>
    );
  }
  if (state.data === null && state.error) {
    const described = describeError(state.error);
    return (
      <ErrorState
        title={described.title}
        description={described.description}
        onRetry={state.reload}
      />
    );
  }
  if (state.data === null) {
    return null;
  }
  return <>{children(state.data)}</>;
}
