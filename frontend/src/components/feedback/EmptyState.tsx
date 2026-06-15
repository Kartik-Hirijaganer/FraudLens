/**
 * Summary: The empty-state card shown when a list/section has no data yet (plan §16
 * Phase 11 empty states). It uses the sage feature-card chrome from DESIGN.md
 * (`ex-empty-state-card`) and optionally hosts a call-to-action so an empty screen still
 * tells the analyst what to do next (e.g. "Import transactions").
 *
 * Key classes:
 * - EmptyStateProps: props (title, optional description + action node).
 *
 * Key functions:
 * - EmptyState: render the empty-state card.
 *
 * Notes:
 * - The optional `action` is any node (typically a Button) so callers wire navigation.
 */
import type { ReactNode } from "react";

export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="gap-md bg-canvas-soft p-3xl flex flex-col items-center rounded-xl text-center">
      <p className="text-display-xs text-ink">{title}</p>
      {description ? <p className="text-body-md text-body">{description}</p> : null}
      {action ? <div className="mt-sm">{action}</div> : null}
    </div>
  );
}
