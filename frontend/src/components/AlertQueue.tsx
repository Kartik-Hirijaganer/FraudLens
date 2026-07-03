/**
 * Summary: The dashboard "Your queue" card (redesign). It renders the analyst's open
 * alerts as scannable rows — severity pill, alert reference, a reason-derived headline,
 * and an amount + relative-age subline — each with a Review CTA that deep-links into the
 * alert-review flow, capped to the top few with a "View all" footer to the full queue.
 * Rows are ordered risk-first then most-recent (matching the queue caption).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AlertQueue: render the open-alert queue card (or an empty state).
 *
 * Notes:
 * - The headline is built from the alert's review flags (never raw ids/PHI); an alert
 *   with no flags degrades to a neutral "Flagged transaction" label.
 */
import type { AlertView } from "../lib/api";
import { formatAgo, formatAlertRef, formatCurrency, humanize } from "../lib/format";
import { riskTone, severityRank } from "../lib/risk";
import { paths } from "../lib/router";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { EmptyState } from "./feedback/EmptyState";

const MAX_QUEUE_ROWS = 4;

interface AlertQueueProps {
  alerts: AlertView[];
  totalOpen: number;
  onSelect: (alertId: string) => void;
}

function headline(alert: AlertView): string {
  const reasons = alert.reviewFlags.map((flag) => flag.reason).filter(Boolean);
  return reasons.length > 0 ? reasons.join(" · ") : "Flagged transaction";
}

function byRiskThenRecency(a: AlertView, b: AlertView): number {
  const rank = severityRank(b.severity) - severityRank(a.severity);
  return rank !== 0 ? rank : b.createdAt.localeCompare(a.createdAt);
}

export function AlertQueue({ alerts, totalOpen, onSelect }: AlertQueueProps) {
  const ordered = [...alerts].sort(byRiskThenRecency).slice(0, MAX_QUEUE_ROWS);

  return (
    <Card className="gap-lg flex flex-col">
      <div className="gap-md flex flex-wrap items-baseline justify-between">
        <h2 className="text-display-xs text-ink">Your queue</h2>
        <span className="text-body-sm text-mute">Sorted by risk, then age</span>
      </div>

      {ordered.length === 0 ? (
        <EmptyState
          title="You're all caught up"
          description="Open alerts appear here when an investigation crosses the alert threshold."
        />
      ) : (
        <>
          <ul className="gap-sm flex flex-col">
            {ordered.map((alert) => (
              <li
                key={alert.alertId}
                className="gap-lg bg-canvas-soft px-lg py-md flex flex-wrap items-center justify-between rounded-lg"
              >
                <div className="gap-lg flex min-w-0 items-center">
                  <Badge tone={riskTone(alert.severity)}>{humanize(alert.severity)}</Badge>
                  <span className="text-body-sm text-ink font-semibold">
                    {formatAlertRef(alert.alertId)}
                  </span>
                  <div className="min-w-0">
                    <p className="text-body-md text-ink truncate font-medium">{headline(alert)}</p>
                    <p className="text-body-sm text-mute">
                      {formatCurrency(alert.amount, alert.currency)} · flagged{" "}
                      {formatAgo(alert.createdAt)}
                    </p>
                  </div>
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  className="shrink-0"
                  onClick={() => onSelect(alert.alertId)}
                >
                  Review
                </Button>
              </li>
            ))}
          </ul>
          <a href={paths.alerts} className="text-body-sm text-ink py-xs self-center font-semibold">
            View all {totalOpen} alerts →
          </a>
        </>
      )}
    </Card>
  );
}
