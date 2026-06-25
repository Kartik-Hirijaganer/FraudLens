/**
 * Summary: Shared sticky decision rail used by investigation and alert review
 * screens. It centralizes the right-column shell so those pages can drop the rail
 * below content on mobile while keeping action controls visually grouped.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - DecisionRail: render the action rail container.
 *
 * Notes:
 * - The rail is only sticky at desktop widths; source order remains content first.
 */
import type { ReactNode } from "react";

import { cx } from "../lib/cx";

interface DecisionRailProps {
  title?: string;
  children: ReactNode;
  className?: string;
}

export function DecisionRail({ title = "Decision", children, className }: DecisionRailProps) {
  return (
    <aside className={cx("lg:sticky lg:top-xl lg:self-start", className)}>
      <div className="gap-md bg-canvas p-xl text-ink flex flex-col rounded-xl">
        {title ? <h2 className="text-display-xs text-ink">{title}</h2> : null}
        {children}
      </div>
    </aside>
  );
}
