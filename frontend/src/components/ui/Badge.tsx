/**
 * Summary: The wise status pill. `positive` uses the pale-green surface with deep
 * positive-green text; `negative` uses the dark maroon surface with light text —
 * both pill-radius per DESIGN.md. These convey in-product status; Wise green is NOT
 * used here (it is the CTA accent only).
 *
 * Key classes:
 * - BadgeProps: props (tone + children).
 *
 * Key functions:
 * - Badge: render a status pill.
 *
 * Notes:
 * - Tones map to the semantic palette (positive/negative), never the brand accent.
 */
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export type BadgeTone = "positive" | "negative";

const TONE_CLASSES: Record<BadgeTone, string> = {
  positive: "bg-primary-pale text-positive-deep",
  negative: "bg-negative-bg text-canvas",
};

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
}

export function Badge({ tone = "positive", children }: BadgeProps) {
  return (
    <span className={cx("rounded-pill px-md py-xs text-body-sm font-semibold", TONE_CLASSES[tone])}>
      {children}
    </span>
  );
}
