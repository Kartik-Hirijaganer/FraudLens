/**
 * Summary: Persisted automatic-screening progress and recoverable observation errors.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ScreeningStatus: distinguish ingestion, screening failure and blocked drafting.
 *
 * Notes:
 * - Status uses semantic colors and a polite live region, never the brand CTA green.
 */
import type { ScreeningProgress } from "../lib/api";

export function ScreeningStatus({
  progress,
  error,
  onRetry,
}: {
  progress: ScreeningProgress | null;
  error: boolean;
  onRetry: () => void;
}) {
  if (!progress?.total && !error) return null;
  return (
    <aside
      aria-label="Screening progress"
      aria-live="polite"
      className="bg-canvas-soft text-body px-lg py-md gap-sm text-body-sm flex flex-wrap items-center rounded-xl"
    >
      {progress?.total ? (
        <>
          <span>
            {progress.completed + progress.failed} of {progress.total} screened
          </span>
          <span>
            {progress.pending} queued · {progress.running} running · {progress.retrying} retrying
          </span>
          {progress.failed > 0 ? (
            <span className="text-negative">
              {progress.failed} failed — open the saved run for details or re-run
            </span>
          ) : null}
          {progress.draftingBlocked > 0 ? (
            <span className="text-warning-content">
              {progress.draftingBlocked} drafting blocked — risk results remain available
            </span>
          ) : null}
        </>
      ) : null}
      {error ? (
        <span className="text-warning-content">
          Progress updates interrupted.{" "}
          <button type="button" className="underline" onClick={onRetry}>
            Retry updates
          </button>
        </span>
      ) : null}
    </aside>
  );
}
