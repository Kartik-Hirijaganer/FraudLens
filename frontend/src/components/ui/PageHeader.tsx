/**
 * Summary: Shared page-header band for analyst screens. It renders the page H1,
 * optional description, action slot, and aside slot inside the sage canvas-soft
 * surface used by Direction A, removing repeated per-page header markup.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - PageHeader: render the shared page heading band.
 *
 * Notes:
 * - Styling uses existing Tailwind theme tokens only; callers provide content, not
 *   layout chrome.
 */
import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  aside?: ReactNode;
}

export function PageHeader({ title, description, actions, aside }: PageHeaderProps) {
  return (
    <header className="gap-lg bg-canvas-soft p-3xl flex flex-col rounded-xl">
      <div className="gap-lg flex flex-col lg:flex-row lg:items-start lg:justify-between">
        <div className="gap-sm flex flex-col">
          <h1 className="text-display-md text-ink">{title}</h1>
          {description ? <p className="text-body-lg text-body">{description}</p> : null}
        </div>
        {aside ? <div className="shrink-0">{aside}</div> : null}
      </div>
      {actions ? <div className="gap-sm flex flex-wrap">{actions}</div> : null}
    </header>
  );
}
