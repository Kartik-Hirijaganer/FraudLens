/**
 * Summary: Accessible disclosure primitive for progressively revealing provenance details. A
 * real button owns `aria-expanded`/`aria-controls`; the controlled panel remains mounted and is
 * toggled with the native `hidden` attribute so its identity and contents stay stable.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Disclosure: render an accessible summary button and always-mounted details panel.
 *
 * Notes:
 * - The primitive owns only disclosure behavior and neutral Wise-token styling; callers supply
 *   the semantic status treatment in the summary content.
 */
import { useId, useState, type ReactNode } from "react";

import { cx } from "../../lib/cx";

interface DisclosureProps {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  panelClassName?: string;
}

export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className,
  panelClassName,
}: DisclosureProps) {
  const panelId = useId();
  const [expanded, setExpanded] = useState(defaultOpen);

  return (
    <div className={cx("bg-canvas-soft rounded-lg", className)}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={panelId}
        className="gap-md p-md flex w-full items-center justify-between rounded-lg text-left"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="min-w-0 grow">{summary}</span>
        <span aria-hidden="true" className="text-body text-body-md shrink-0 font-semibold">
          {expanded ? "−" : "+"}
        </span>
      </button>
      <div
        id={panelId}
        hidden={!expanded}
        className={cx("border-canvas p-md border-t", panelClassName)}
      >
        {children}
      </div>
    </div>
  );
}
