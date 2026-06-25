/**
 * Summary: The alerts queue table wrapper (plan §16 Phase 11 AlertTable). It defines
 * the alert-specific columns over the shared DataTable primitive: risk dot, status
 * badge, amount, force-review flags, age, and a Review action that opens detail/review.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AlertTable: render the alerts DataTable wrapper (or an empty state).
 *
 * Notes:
 * - AlertTable intentionally stays as a thin domain wrapper rather than duplicating
 *   table chrome in pages.
 */
import { STATUS_LABELS, type AlertView } from "../lib/api";
import { formatAge, formatCurrency } from "../lib/format";
import { riskTone } from "../lib/risk";
import { RiskDot } from "./RiskDot";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { DataTable, type Column } from "./ui/DataTable";
import { EmptyState } from "./feedback/EmptyState";

interface AlertTableProps {
  alerts: AlertView[];
  onSelect: (alertId: string) => void;
}

export function AlertTable({ alerts, onSelect }: AlertTableProps) {
  const columns: Column<AlertView>[] = [
    {
      id: "severity",
      header: "Severity",
      cell: (alert) => <RiskDot band={alert.severity} showLabel />,
    },
    {
      id: "status",
      header: "Status",
      cell: (alert) => <Badge tone={riskTone(alert.severity)}>{STATUS_LABELS[alert.status]}</Badge>,
    },
    {
      id: "amount",
      header: "Amount",
      align: "right",
      cell: (alert) => (
        <span className="text-body-sm text-ink">
          {formatCurrency(alert.amount, alert.currency)}
        </span>
      ),
    },
    {
      id: "flags",
      header: "Flags",
      cell: (alert) => <span className="text-body text-body-sm">{alert.reviewFlags.length}</span>,
    },
    {
      id: "age",
      header: "Age",
      cell: (alert) => <span className="text-body text-body-sm">{formatAge(alert.createdAt)}</span>,
    },
    {
      id: "action",
      header: "Actions",
      srOnlyHeader: true,
      cell: (alert) => (
        <Button variant="secondary" onClick={() => onSelect(alert.alertId)}>
          Review
        </Button>
      ),
    },
  ];

  return (
    <DataTable
      caption="Alerts"
      columns={columns}
      rows={alerts}
      rowKey={(alert) => alert.alertId}
      onRowClick={(alert) => onSelect(alert.alertId)}
      empty={
        <EmptyState
          title="No alerts"
          description="Alerts appear when an investigation crosses the alert threshold."
        />
      }
    />
  );
}
