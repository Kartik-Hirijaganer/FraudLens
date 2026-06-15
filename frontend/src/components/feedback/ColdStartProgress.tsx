/**
 * Summary: The cold-start progress indicator (plan §2 NFR cold start ≤75s, §16 Phase 11
 * "cold-start progress"). The backend scales to zero, so the first request after idle can
 * take up to ~75s; this reassures the analyst that the service is waking rather than
 * stalled. The bar pulses via `motion-safe` (static for reduced motion) and uses the
 * neutral ink palette, never the brand green (which is reserved for CTAs).
 *
 * Key classes:
 * - ColdStartProgressProps: props (optional reassurance message).
 *
 * Key functions:
 * - ColdStartProgress: render the cold-start progress message + indeterminate bar.
 *
 * Notes:
 * - The bar is `role="progressbar"` without a value (indeterminate) — duration is unknown.
 */
export interface ColdStartProgressProps {
  message?: string;
}

export function ColdStartProgress({
  message = "Waking the service — this can take a moment…",
}: ColdStartProgressProps) {
  return (
    <div className="gap-sm flex flex-col">
      <p className="text-body-sm text-body">{message}</p>
      <div
        role="progressbar"
        aria-label="Starting up"
        className="h-xs rounded-pill bg-canvas-soft w-full overflow-hidden"
      >
        <div className="rounded-pill bg-ink h-full w-1/3 motion-safe:animate-pulse" />
      </div>
    </div>
  );
}
