/**
 * Summary: The wise status pill, keyed to the shared `StatusTone` palette so risk
 * bands and severities (via `riskTone`) render with one consistent vocabulary. Tones
 * map onto the semantic palette — positive (pale-green/positive-deep), warning
 * (yellow/warning-content), negative (maroon/white), neutral (sage/body) — all
 * pill-radius per DESIGN.md. Wise green is NEVER a tone here (it is the CTA accent only).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Badge: render a status pill.
 *
 * Notes:
 * - Defaults to the positive tone; pass a `StatusTone` for warning/negative/neutral.
 * - Content is centered, so a caller can pass a fixed-width `className` (e.g. `w-20`) to
 *   line badges up into a column without the label length changing the pill width.
 */
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import type { StatusTone } from "../../lib/risk";

const TONE_CLASSES: Record<StatusTone, string> = {
  positive: "bg-primary-pale text-positive-deep",
  warning: "bg-warning text-warning-content",
  negative: "bg-negative-bg text-canvas",
  neutral: "bg-canvas-soft text-body",
};

interface BadgeProps {
  tone?: StatusTone;
  className?: string;
  children: ReactNode;
}

export function Badge({ tone = "positive", className, children }: BadgeProps) {
  return (
    <span
      className={cx(
        "rounded-pill px-md py-xs text-body-sm inline-flex items-center justify-center font-semibold",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
