/**
 * Summary: Shared vertical timeline for alert and investigation activity. It
 * renders title, metadata, and optional body text using the Wise surface tokens so
 * activity lists no longer duplicate row structure.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Timeline: render ordered activity items.
 *
 * Notes:
 * - Empty-state copy remains the caller's responsibility because each workflow has
 *   different empty semantics.
 */
import type { ReactNode } from "react";

interface TimelineItem {
  id: string;
  title: ReactNode;
  meta: ReactNode;
  body?: ReactNode;
}

interface TimelineProps {
  items: TimelineItem[];
}

export function Timeline({ items }: TimelineProps) {
  return (
    <ol className="gap-sm flex flex-col">
      {items.map((item) => (
        <li
          key={item.id}
          className="gap-xxs border-canvas-soft pt-sm flex flex-col border-t first:border-0 first:pt-0"
        >
          <span className="text-body-sm text-ink font-semibold">{item.title}</span>
          <span className="text-caption text-mute">{item.meta}</span>
          {item.body ? <span className="text-body-sm text-body">{item.body}</span> : null}
        </li>
      ))}
    </ol>
  );
}
