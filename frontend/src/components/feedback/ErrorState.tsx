/**
 * Summary: The inline error card shown when a fetch/action fails (plan §16 Phase 11
 * error + retry states). It surfaces only safe copy (a title + PHI-free description from
 * `lib/errors.ts`) and an optional Retry button so the analyst can recover without a
 * reload. It never renders a stack trace, raw body, or internal detail (FraudLens
 * governance: no internals in user-visible errors).
 *
 * Key classes:
 * - ErrorStateProps: props (optional title/description + optional onRetry handler).
 *
 * Key functions:
 * - ErrorState: render the error card with an optional retry action.
 *
 * Notes:
 * - When `onRetry` is omitted the card is informational only (no button).
 */
import { Button } from "../ui/Button";

export interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="gap-md bg-canvas p-xl flex flex-col items-start rounded-xl">
      <p className="text-display-xs text-negative-darkest">{title}</p>
      {description ? <p className="text-body-md text-body">{description}</p> : null}
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
