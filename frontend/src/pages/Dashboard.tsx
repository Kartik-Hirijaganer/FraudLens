/**
 * Summary: The analyst landing page (plan §16 Phase 11 Dashboard, wired in Phase 12). It reads the
 * tenant-scoped `/dashboard/metrics` aggregate for its stat cards — open alerts, investigation-run
 * health, the active/canary model pointer, and recent transaction volume — and lists the open-alert
 * queue (which deep-links into review) from the alerts endpoint. Loading, error+retry, and empty
 * states all flow through the shared AsyncBoundary, and the metrics + alerts load concurrently.
 *
 * Key classes:
 * - DashboardProps: props (an injectable ApiClient for tests).
 *
 * Key functions:
 * - Dashboard: render the metrics stat cards + open-alerts queue.
 *
 * Notes:
 * - A null active model label degrades to an em dash rather than failing the page, so a fresh
 *   environment without a promoted model still renders.
 */
import { useCallback } from "react";

import { AlertTable } from "../components/AlertTable";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { Card } from "../components/ui/Card";
import { apiClient, type AlertView, type ApiClient, type DashboardMetrics } from "../lib/api";
import { navigate, paths } from "../lib/router";
import { useAsync } from "../lib/useAsync";

interface DashboardData {
  metrics: DashboardMetrics;
  openAlerts: AlertView[];
}

export interface DashboardProps {
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
      <header className="gap-sm bg-canvas-soft p-3xl flex flex-col rounded-xl">
        <h1 className="text-display-md text-ink">Investigations</h1>
        <p className="text-body-lg text-body">
          Triage flagged transactions, review SAR drafts, and operate the scoring model.
        </p>
      </header>
      <AsyncBoundary state={state}>
        {({ metrics, openAlerts }) => (
          <div className="gap-xl flex flex-col">
            <div className="gap-lg grid sm:grid-cols-4">
              <Card className="gap-xs flex flex-col">
                <span className="text-caption text-mute">Open alerts</span>
                <span className="text-display-sm text-ink">{metrics.alerts.open}</span>
              </Card>
              <Card className="gap-xs flex flex-col">
                <span className="text-caption text-mute">Investigations</span>
                <span className="text-display-sm text-ink">{metrics.runs.completed}</span>
                <span className="text-caption text-mute">{metrics.runs.total} total runs</span>
              </Card>
              <Card className="gap-xs flex flex-col">
                <span className="text-caption text-mute">Active model</span>
                <span className="text-display-xs text-ink">
                  {metrics.modelHealth.activeVersionLabel ?? "—"}
                </span>
                {metrics.modelHealth.canaryVersionLabel ? (
                  <span className="text-caption text-mute">
                    canary {metrics.modelHealth.canaryVersionLabel} @{" "}
                    {metrics.modelHealth.canaryPercent}%
                  </span>
                ) : null}
              </Card>
              <Card className="gap-xs flex flex-col">
                <span className="text-caption text-mute">Recent transactions</span>
                <span className="text-display-sm text-ink">{metrics.transactions.total}</span>
              </Card>
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
