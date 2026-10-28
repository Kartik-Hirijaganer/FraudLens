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
 * - Queue selection lives in the URL so dashboard links and browser Back keep their filter.
 */
import { useCallback } from "react";

import { AlertTable } from "../components/AlertTable";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { apiClient, type CaseQueue, type ApiClient } from "../lib/api";
import { navigate, paths, useHashRoute } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { useScreeningRefresh } from "../lib/useScreeningRefresh";

interface AlertsProps {
  client?: ApiClient;
}

export function Alerts({ client = apiClient }: AlertsProps) {
  const route = useHashRoute();
  const queue = route.name === "alerts" ? route.queue : undefined;
  const load = useCallback(() => client.listAlerts({ queue, limit: 100 }), [client, queue]);
  const state = useAsync(load, [client, queue]);
  useScreeningRefresh(client, state.reload);

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader title="Alerts" description="Review and resolve flagged investigations." />
      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <span className="text-body-sm text-body">Case queues</span>
          <SegmentedControl
            ariaLabel="Case queues"
            options={[
              { value: "", label: "All cases" },
              { value: "available", label: "Available" },
              { value: "mine", label: "My cases" },
              { value: "awaiting_review", label: "Awaiting my review" },
              { value: "returned", label: "Returned" },
              { value: "closed", label: "Closed" },
            ]}
            value={queue ?? ""}
            onChange={(value) =>
              navigate(value ? paths.alertsByQueue(value as CaseQueue) : paths.alerts)
            }
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
