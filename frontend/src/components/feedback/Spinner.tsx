/**
 * Summary: A small inline loading spinner for in-flight actions (a submitting button, a
 * page fetch). It spins via `motion-safe:animate-spin` so reduced-motion users get a
 * static ring, and exposes `role="status"` + an accessible label so the loading state is
 * announced.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Spinner: render an accessible loading spinner.
 *
 * Notes:
 * - Uses the ink/sage palette (never the brand green) since it conveys progress, not a CTA.
 */
import { cx } from "../../lib/cx";

interface SpinnerProps {
  label?: string;
}

export function Spinner({ label = "Loading" }: SpinnerProps) {
  return (
    <span role="status" aria-label={label} className="inline-flex">
      <span
        aria-hidden="true"
        className={cx(
          "size-lg rounded-full border-2 border-canvas-soft border-t-ink motion-safe:animate-spin",
        )}
      />
    </span>
  );
}
