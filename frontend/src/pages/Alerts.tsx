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
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { apiClient, type AlertStatus, type ApiClient } from "../lib/api";
import { ALERT_STATUS_OPTIONS } from "../lib/options";
import { navigate, paths } from "../lib/router";
import { useAsync } from "../lib/useAsync";

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
      <PageHeader title="Alerts" description="Review and resolve flagged investigations." />
      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <span className="text-body-sm text-body">Filter by status</span>
          <SegmentedControl
            ariaLabel="Filter by status"
            options={ALERT_STATUS_OPTIONS}
            value={status}
            onChange={setStatus}
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
