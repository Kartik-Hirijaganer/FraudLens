/**
 * Summary: The alerts queue page (plan §16 Phase 11; FR-7/FR-8). It lists the agency's
 * alerts with a status filter and deep-links each into the AlertDetail review page.
 * Loading / empty / error+retry flow through the shared AsyncBoundary + AlertTable.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Alerts: render the filterable alerts queue.
 *
 * Notes:
 * - The empty status filter lists all statuses; a chosen value is a verified `AlertStatus`.
 */
import { useCallback, useState } from "react";

import { AlertTable } from "../components/AlertTable";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Select";
import { apiClient, type AlertStatus, type ApiClient } from "../lib/api";
import { navigate, paths } from "../lib/router";
import { useAsync } from "../lib/useAsync";

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "in_review", label: "In review" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

interface AlertsProps {
  client?: ApiClient;
}

export function Alerts({ client = apiClient }: AlertsProps) {
  const [status, setStatus] = useState("");
  const load = useCallback(
    () => client.listAlerts({ status: status ? (status as AlertStatus) : undefined, limit: 100 }),
    [client, status],
  );
  const state = useAsync(load, [client, status]);

  return (
    <section className="gap-xl flex flex-col">
      <header className="gap-sm bg-canvas-soft p-3xl flex flex-col rounded-xl">
        <h1 className="text-display-md text-ink">Alerts</h1>
        <p className="text-body-lg text-body">Review and resolve flagged investigations.</p>
      </header>
      <Card className="gap-lg flex flex-col">
        <div className="max-w-xs">
          <Select
            label="Filter by status"
            options={STATUS_OPTIONS}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          />
        </div>
        <AsyncBoundary state={state}>
          {(data) => (
            <AlertTable
              alerts={data.alerts}
              onSelect={(alertId) => navigate(paths.alertDetail(alertId))}
            />
          )}
        </AsyncBoundary>
      </Card>
    </section>
  );
}
