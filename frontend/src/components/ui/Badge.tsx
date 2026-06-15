/**
 * Summary: The wise status pill, keyed to the shared `StatusTone` palette so risk
 * bands and severities (via `riskTone`) render with one consistent vocabulary. Tones
 * map onto the semantic palette — positive (pale-green/positive-deep), warning
 * (yellow/warning-content), negative (maroon/white), neutral (sage/body) — all
 * pill-radius per DESIGN.md. Wise green is NEVER a tone here (it is the CTA accent only).
 *
 * Key classes:
 * - BadgeProps: props (tone + children).
 *
 * Key functions:
 * - Badge: render a status pill.
 *
 * Notes:
 * - Defaults to the positive tone; pass a `StatusTone` for warning/negative/neutral.
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

export interface BadgeProps {
  tone?: StatusTone;
  children: ReactNode;
}

export function Badge({ tone = "positive", children }: BadgeProps) {
  return (
    <span className={cx("rounded-pill px-md py-xs text-body-sm font-semibold", TONE_CLASSES[tone])}>
      {children}
    </span>
  );
}
