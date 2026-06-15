/**
 * Summary: The alerts queue table (plan §16 Phase 11 AlertTable). Each row shows the
 * severity (as a semantic badge), status, the count of force-review flags, and when the
 * alert was raised, with an Open action that selects it for detail/review. Renders an
 * EmptyState when there are no alerts.
 *
 * Key classes:
 * - AlertTableProps: props (the alerts + an onSelect callback).
 *
 * Key functions:
 * - AlertTable: render the alerts table (or an empty state).
 *
 * Notes:
 * - Severity uses `riskTone` so it matches the gauge/badge colouring across the app.
 */
import type { AlertView } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { riskTone } from "../lib/risk";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { EmptyState } from "./feedback/EmptyState";

export interface AlertTableProps {
  alerts: AlertView[];
  onSelect: (alertId: string) => void;
}

export function AlertTable({ alerts, onSelect }: AlertTableProps) {
  if (alerts.length === 0) {
    return (
      <EmptyState
        title="No alerts"
        description="Alerts appear when an investigation crosses the alert threshold."
      />
    );
  }
  return (
    <table className="w-full border-collapse text-left">
      <thead>
        <tr className="text-caption text-mute">
          <th scope="col" className="px-lg py-md font-semibold">
            Severity
          </th>
          <th scope="col" className="px-lg py-md font-semibold">
            Status
          </th>
          <th scope="col" className="px-lg py-md font-semibold">
            Flags
          </th>
          <th scope="col" className="px-lg py-md font-semibold">
            Raised
          </th>
          <th scope="col" className="px-lg py-md">
            <span className="sr-only">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {alerts.map((alert) => (
          <tr key={alert.alertId} className="border-canvas-soft border-t">
            <td className="px-lg py-md">
              <Badge tone={riskTone(alert.severity)}>{humanize(alert.severity)}</Badge>
            </td>
            <td className="px-lg py-md text-body-sm text-ink">{humanize(alert.status)}</td>
            <td className="px-lg py-md text-body-sm text-body">{alert.reviewFlags.length}</td>
            <td className="px-lg py-md text-body-sm text-body">
              {formatDateTime(alert.createdAt)}
            </td>
            <td className="px-lg py-md">
              <Button variant="secondary" onClick={() => onSelect(alert.alertId)}>
                Open
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
