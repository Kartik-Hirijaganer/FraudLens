/**
 * Summary: Shared metric tile for KPI cards and metric definition lists. It
 * centralizes the label/value/hint markup currently repeated across dashboard and
 * model surfaces while preserving the Wise card treatment.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - StatTile: render one labeled metric as a card or definition-list group.
 *
 * Notes:
 * - The `as="dl"` mode renders a bare div suitable inside an existing `<dl>`;
 *   default mode renders a card-like tile.
 */
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

interface StatTileProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  emphasis?: "lg" | "md";
  as?: "div" | "dl";
  className?: string;
}

export function StatTile({
  label,
  value,
  hint,
  emphasis = "lg",
  as = "div",
  className,
}: StatTileProps) {
  const valueClass = emphasis === "lg" ? "text-display-sm" : "text-display-xs";
  const content = (
    <>
      <dt className="text-caption text-mute">{label}</dt>
      <dd className={cx(valueClass, "text-ink")}>{value}</dd>
      {hint ? <dd className="text-caption text-mute">{hint}</dd> : null}
    </>
  );

  if (as === "dl") {
    return <div className={cx("gap-xxs flex flex-col", className)}>{content}</div>;
  }

  return (
    <dl className={cx("gap-xs rounded-xl bg-canvas p-xl flex flex-col", className)}>{content}</dl>
  );
}
