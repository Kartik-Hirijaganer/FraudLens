/**
 * Summary: The analyst landing page (plan §16 Phase 11 Dashboard, wired in Phase 12). It reads the
 * tenant-scoped `/dashboard/metrics` aggregate for its stat cards — open alerts, investigation-run
 * health, the active/canary model pointer, and recent transaction volume — and lists the open-alert
 * queue (which deep-links into review) from the alerts endpoint. Loading, error+retry, and empty
 * states all flow through the shared AsyncBoundary, and the metrics + alerts load concurrently.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Dashboard: render the metrics stat cards + open-alerts queue.
 *
 * Notes:
 * - A null active model label degrades to an em dash rather than failing the page, so a fresh
 * environment without a promoted model still renders.
 */
import { useCallback } from "react";

import { AlertTable } from "../components/AlertTable";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { StatTile } from "../components/ui/StatTile";
import { apiClient, type AlertView, type ApiClient, type DashboardMetrics } from "../lib/api";
import { navigate, paths } from "../lib/router";
import { useAsync } from "../lib/useAsync";

interface DashboardData {
  metrics: DashboardMetrics;
  openAlerts: AlertView[];
}

interface DashboardProps {
  client?: ApiClient;
}

export function Dashboard({ client = apiClient }: DashboardProps) {
  const load = useCallback(async (): Promise<DashboardData> => {
    const [metrics, alerts] = await Promise.all([
      client.getDashboardMetrics(),
      client.listAlerts({ status: "open", limit: 50 }),
    ]);
    return { metrics, openAlerts: alerts.alerts };
  }, [client]);
  const state = useAsync(load, [client]);

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Investigations"
        description="Triage flagged transactions, review SAR drafts, and operate the scoring model."
      />
      <AsyncBoundary state={state}>
        {({ metrics, openAlerts }) => (
          <div className="gap-xl flex flex-col">
            <div className="gap-lg grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4">
              <StatTile label="Open alerts" value={metrics.alerts.open} />
              <StatTile
                label="Investigations"
                value={metrics.runs.completed}
                hint={`${metrics.runs.total} total runs`}
              />
              <StatTile
                label="Active model"
                value={metrics.modelHealth.activeVersionLabel ?? "—"}
                hint={
                  metrics.modelHealth.canaryVersionLabel
                    ? `canary ${metrics.modelHealth.canaryVersionLabel} @ ${metrics.modelHealth.canaryPercent}%`
                    : undefined
                }
                emphasis="md"
              />
              <StatTile label="Recent transactions" value={metrics.transactions.total} />
            </div>
            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">Open alerts</h2>
              <AlertTable
                alerts={openAlerts}
                onSelect={(alertId) => navigate(paths.alertDetail(alertId))}
              />
            </Card>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}
