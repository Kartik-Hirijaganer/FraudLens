/**
 * Summary: The analyst landing page (plan §16 Phase 11 Dashboard, redesigned; band mix added in
 * the portfolio-demo story Phase 3b). It reads
 * the tenant-scoped `/dashboard/metrics` aggregate plus the open-alert list and greets
 * the analyst by time of day, calls out the high-risk backlog, and surfaces four headline
 * KPI cards (open alerts + severity mix, in-review load, SARs approved, active-model health)
 * plus the transaction risk-band mix above the open-alert triage queue. Loading, error+retry, and
 * empty states all flow through the shared AsyncBoundary, and the metrics + alerts load
 * concurrently.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Dashboard: render the greeting, KPI cards, risk-band mix, and open-alert queue.
 *
 * Notes:
 * - A null active model label degrades to an em dash rather than failing the page, so a
 *   fresh environment without a promoted model still renders.
 * - The severity mix and high-risk callout are derived from the loaded open alerts, so the
 *   headline stays consistent with the queue below it.
 * - `RiskBandBar` reads `metrics.transactions`, which this page ALREADY fetches — it adds no
 *   second request and invents no counter.
 * - The greeting name is the session's display identity (the picked persona, or the verified
 *   `/me` display name); with none it greets without a name rather than naming anyone.
 */
import { useCallback } from "react";

import { AlertQueue } from "../components/AlertQueue";
import { DashboardSkeleton } from "../components/DashboardSkeleton";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { MetricCard } from "../components/MetricCard";
import { RiskBandBar } from "../components/RiskBandBar";
import { apiClient, type AlertView, type ApiClient, type DashboardMetrics } from "../lib/api";
import { formatModelVersion, greeting } from "../lib/format";
import { riskTone, severityCounts, type StatusTone } from "../lib/risk";
import { navigate, paths } from "../lib/router";
import { useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";

interface DashboardData {
  metrics: DashboardMetrics;
  openAlerts: AlertView[];
}

interface DashboardProps {
  client?: ApiClient;
}

function subtitle(open: number, highRisk: number): string {
  if (open === 0) {
    return "You're all caught up — no open alerts to review.";
  }
  const lead = `You have ${open} open alert${open === 1 ? "" : "s"}.`;
  if (highRisk === 0) {
    return `${lead} None are high-risk right now.`;
  }
  return `${lead} Start with the ${highRisk} high-risk one${highRisk === 1 ? "" : "s"}.`;
}

function modelHint(metrics: DashboardMetrics): { text: string; tone: StatusTone } {
  const { activeVersionLabel, canaryVersionLabel, canaryPercent, latestDriftSeverity } =
    metrics.modelHealth;
  if (!activeVersionLabel) {
    return { text: "No model promoted", tone: "neutral" };
  }
  if (canaryVersionLabel) {
    return { text: `Canary ${canaryVersionLabel} @ ${canaryPercent}%`, tone: "warning" };
  }
  const tone = latestDriftSeverity ? riskTone(latestDriftSeverity) : "positive";
  const drift = latestDriftSeverity ? `drift ${latestDriftSeverity}` : "no drift reported";
  return { text: `Healthy · ${drift}`, tone };
}

export function Dashboard({ client = apiClient }: DashboardProps) {
  const session = useSession();
  const load = useCallback(async (): Promise<DashboardData> => {
    const [metrics, alerts] = await Promise.all([
      client.getDashboardMetrics(),
      client.listAlerts({ status: "open", limit: 50 }),
    ]);
    return { metrics, openAlerts: alerts.alerts };
  }, [client]);
  const state = useAsync(load, [client]);

  return (
    <AsyncBoundary state={state} skeleton={<DashboardSkeleton />}>
      {({ metrics, openAlerts }) => {
        const counts = severityCounts(openAlerts.map((alert) => alert.severity));
        const open = metrics.alerts.open;
        const model = modelHint(metrics);
        return (
          <section className="gap-2xl flex flex-col">
            <header className="gap-sm flex flex-col">
              <h1 className="text-display-sm md:text-display-md text-ink">
                {session ? `${greeting()}, ${session.analyst.name}` : greeting()}
              </h1>
              <p className="text-body-lg text-body">{subtitle(open, counts.high)}</p>
            </header>

            <div className="gap-lg grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                label="Open alerts"
                value={open}
                hint={`${counts.high} high · ${counts.medium} medium · ${counts.low} low`}
              />
              <MetricCard
                label="In review"
                value={metrics.alerts.inReview}
                hint={`${metrics.alerts.escalated} escalated`}
              />
              <MetricCard
                label="SARs approved"
                value={metrics.sar.approved}
                hint={`${metrics.sar.total} drafted`}
              />
              <MetricCard
                label="Active model"
                value={formatModelVersion(metrics.modelHealth.activeVersionLabel)}
                hint={model.text}
                hintTone={model.tone}
              />
            </div>

            <RiskBandBar metrics={metrics.transactions} />

            <AlertQueue
              alerts={openAlerts}
              totalOpen={open}
              onSelect={(alertId) => navigate(paths.alertDetail(alertId))}
            />
          </section>
        );
      }}
    </AsyncBoundary>
  );
}
