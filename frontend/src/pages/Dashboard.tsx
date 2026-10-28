/**
 * Summary: The analyst landing page (plan §16 Phase 11 Dashboard, redesigned; band mix added in
 * the portfolio-demo story Phase 3b). It reads
 * the tenant-scoped `/dashboard/metrics` aggregate plus the caller's alert queue and greets
 * the user by time of day, calls out the actionable backlog, and surfaces four headline
 * KPI cards (triage/review queue, preparation load, SARs approved, active-model health)
 * plus the transaction risk-band mix above the queue. Loading, error+retry, and
 * empty states all flow through the shared AsyncBoundary, and the metrics + alerts load
 * concurrently.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Dashboard: render the greeting, KPI cards, risk-band mix, and role-appropriate alert queue.
 *
 * Notes:
 * - A null active model label degrades to an em dash rather than failing the page, so a
 *   fresh environment without a promoted model still renders.
 * - Reviewers/Admins use the server's person-filtered awaiting-review queue and total;
 *   contributors and closed rounds are excluded before pagination.
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
import { formatModelBuild, formatModelVersion, greeting } from "../lib/format";
import { riskTone, severityCounts, type StatusTone } from "../lib/risk";
import { navigate, paths } from "../lib/router";
import { hasPermission, useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";
import { useScreeningRefresh } from "../lib/useScreeningRefresh";

interface DashboardData {
  metrics: DashboardMetrics;
  alerts: AlertView[];
  total: number;
}

interface DashboardProps {
  client?: ApiClient;
}

function subtitle(open: number, highRisk: number, checking: boolean): string {
  if (checking) {
    return open === 0
      ? "No cases are awaiting your independent review."
      : `You have ${open} case${open === 1 ? "" : "s"} awaiting your independent review.`;
  }
  if (open === 0) {
    return "No open alerts right now. Check Alerts for cases already in progress.";
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
    return {
      text: `Canary ${formatModelVersion(canaryVersionLabel)} · ${canaryPercent}% traffic`,
      tone: "warning",
    };
  }
  const tone = latestDriftSeverity ? riskTone(latestDriftSeverity) : "positive";
  const drift = latestDriftSeverity ? `drift ${latestDriftSeverity}` : "no drift reported";
  return { text: `Healthy · ${drift}`, tone };
}

export function Dashboard({ client = apiClient }: DashboardProps) {
  const session = useSession();
  const checking = hasPermission(session, "reviewSar");
  const load = useCallback(async (): Promise<DashboardData> => {
    const [metrics, alerts] = await Promise.all([
      client.getDashboardMetrics(),
      client.listAlerts(
        checking ? { queue: "awaiting_review", limit: 50 } : { status: "open", limit: 50 },
      ),
    ]);
    return { metrics, alerts: alerts.alerts, total: alerts.total };
  }, [client, checking]);
  const state = useAsync(load, [load, session?.email, session?.agencyId]);
  useScreeningRefresh(client, state.reload);

  return (
    <AsyncBoundary state={state} skeleton={<DashboardSkeleton />}>
      {({ metrics, alerts, total }) => {
        const counts = severityCounts(alerts.map((alert) => alert.severity));
        const model = modelHint(metrics);
        const activeModelLabel = metrics.modelHealth.activeVersionLabel;
        const activeModelBuild = formatModelBuild(activeModelLabel);
        return (
          <section className="gap-2xl flex flex-col">
            <header className="gap-sm flex flex-col">
              <h1 className="text-display-sm md:text-display-md text-ink">
                {session ? `${greeting()}, ${session.analyst.name}` : greeting()}
              </h1>
              <p className="text-body-lg text-body">{subtitle(total, counts.high, checking)}</p>
            </header>

            <div className="gap-lg grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                label={checking ? "Awaiting my review" : "Open alerts"}
                value={total}
                hint={
                  checking
                    ? "Submitted cases you can independently review"
                    : `${counts.high} high · ${counts.medium} medium · ${counts.low} low`
                }
              />
              <MetricCard
                label="In preparation"
                value={metrics.alerts.inReview + metrics.alerts.pendingReview}
                hint={`${metrics.alerts.escalated} awaiting review across the agency`}
              />
              <MetricCard
                label="SARs approved"
                value={metrics.sar.approved}
                hint={`${metrics.sar.total} drafted`}
              />
              <MetricCard
                label="Active model"
                value={formatModelVersion(activeModelLabel)}
                detail={
                  activeModelBuild ? (
                    <span title={`Registry ID: ${activeModelLabel}`}>{activeModelBuild}</span>
                  ) : undefined
                }
                hint={model.text}
                hintTone={model.tone}
              />
            </div>

            <RiskBandBar metrics={metrics.transactions} />

            <AlertQueue
              alerts={alerts}
              totalOpen={total}
              checking={checking}
              onSelect={(alertId) => navigate(paths.alertDetail(alertId))}
            />
          </section>
        );
      }}
    </AsyncBoundary>
  );
}
