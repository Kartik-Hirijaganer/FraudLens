/**
 * Summary: The dashboard KPI card (redesign). A white `canvas` tile carrying an
 * uppercased mono-caps eyebrow label, an oversized display numeral, and an optional
 * hint line that may lead with a semantic status dot (e.g. model health). It gives the
 * landing page's headline metrics a distinct, scannable voice separate from the compact
 * `StatTile` definition-list tile used inside dense admin panels.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - MetricCard: render one headline metric (eyebrow + value + optional dotted hint).
 *
 * Notes:
 * - `hintTone` reuses the shared `StatusTone` palette so the dot colour matches Badge /
 *   RiskDot; Wise green is never used here (it is the CTA accent, not a status colour).
 */
import type { ReactNode } from "react";

import { cx } from "../lib/cx";
import { toneDotClass, type StatusTone } from "../lib/risk";

interface MetricCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  hintTone?: StatusTone;
}

export function MetricCard({ label, value, hint, hintTone }: MetricCardProps) {
  return (
    <div className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
      <p className="text-caption text-mute font-semibold uppercase tracking-wide">{label}</p>
      <p className="text-display-md text-ink">{value}</p>
      {hint ? (
        <p className="gap-xs text-body-sm text-mute flex items-center">
          {hintTone ? (
            <span
              aria-hidden="true"
              className={cx("h-sm w-sm rounded-full", toneDotClass(hintTone))}
            />
          ) : null}
          <span>{hint}</span>
        </p>
      ) : null}
    </div>
  );
}
