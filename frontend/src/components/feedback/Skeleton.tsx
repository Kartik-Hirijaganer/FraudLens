/**
 * Summary: A neutral placeholder block shown while data loads (plan §14 / §16 Phase 11
 * skeleton states). It pulses on the sage surface using `motion-safe:animate-pulse`, so
 * the shimmer is automatically suppressed for users who prefer reduced motion (CSS-level,
 * no JS) — the WCAG-friendly default.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Skeleton: render a pulsing placeholder block.
 *
 * Notes:
 * - It is decorative (`aria-hidden`) so screen readers announce the eventual content, not
 * the placeholder.
 */
import { cx } from "../../lib/cx";

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cx(
        "rounded-md bg-canvas-soft motion-safe:animate-pulse",
        className ?? "h-lg w-full",
      )}
    />
  );
}
