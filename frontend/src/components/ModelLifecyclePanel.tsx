/**
 * Summary: The admin model-lifecycle control surface (plan §5.4, §10.5, §16 Phase 11
 * ModelLifecyclePanel). It shows the live deployment pointer (active + any canary +
 * previous), the registry versions with the human-gated actions valid for each status
 * (candidate → promote to shadow → approve → canary 5/25/50/100 → activate), a rollback /
 * canary-evaluate control, a retrain trigger, and the advisory drift reports. It is purely
 * presentational: every action invokes a callback the page wires to the API + toasts, and
 * `busy` disables actions while a request is in flight (double-submit guard).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ModelLifecyclePanel: render the deployment summary, version actions, and drift list.
 *
 * Notes:
 * - Canary ramp order is enforced server-side; the panel shows the steps and surfaces an
 * illegal transition as an error toast (the page maps the envelope code via lib/errors).
 */
import type {
  CanaryPercent,
  DeploymentResponse,
  DriftReportView,
  ModelVersionResponse,
} from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { CANARY_RAMP_STEPS, MODEL_METRIC_DEFINITIONS, extractModelMetrics } from "../lib/options";
import { riskTone } from "../lib/risk";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { StatTile } from "./ui/StatTile";
import { EmptyState } from "./feedback/EmptyState";

interface ModelLifecyclePanelProps {
  versions: ModelVersionResponse[];
  deployment: DeploymentResponse | null;
  driftReports: DriftReportView[];
  busy?: boolean;
  onTriggerTraining: () => void;
  onPromoteShadow: (versionId: string) => void;
  onApprove: (versionId: string) => void;
  onSetCanary: (versionId: string, percent: CanaryPercent) => void;
  onRollback: () => void;
  onEvaluateCanary: () => void;
}

function metricText(metrics: Record<string, unknown>): string | null {
  const extracted = extractModelMetrics(metrics);
  const displayed = MODEL_METRIC_DEFINITIONS.map((definition) => {
    const value = extracted[definition.key];
    return value === null ? null : `${definition.label} ${definition.format(value)}`;
  }).filter((value): value is string => value !== null);
  return displayed.length > 0 ? displayed.join(" · ") : null;
}

export function ModelLifecyclePanel({
  versions,
  deployment,
  driftReports,
  busy = false,
  onTriggerTraining,
  onPromoteShadow,
  onApprove,
  onSetCanary,
  onRollback,
  onEvaluateCanary,
}: ModelLifecyclePanelProps) {
  const canaryActions = (versionId: string) =>
    CANARY_RAMP_STEPS.map((percent: CanaryPercent) => (
      <Button
        key={percent}
        variant="tertiary"
        disabled={busy}
        onClick={() => onSetCanary(versionId, percent)}
      >
        {percent === 100 ? "Activate (100%)" : `Canary ${percent}%`}
      </Button>
    ));

  const versionActions = (version: ModelVersionResponse) => {
    if (version.status === "candidate") {
      return (
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => onPromoteShadow(version.versionId)}
        >
          Promote to shadow
        </Button>
      );
    }
    if (version.status === "shadow") {
      return (
        <>
          <Button variant="secondary" disabled={busy} onClick={() => onApprove(version.versionId)}>
            Approve
          </Button>
          {canaryActions(version.versionId)}
        </>
      );
    }
    if (version.status === "canary") {
      return <>{canaryActions(version.versionId)}</>;
    }
    return null;
  };

  return (
    <div className="gap-xl flex flex-col">
      <Card className="gap-md flex flex-col">
        <div className="gap-md flex flex-wrap items-center justify-between">
          <h3 className="text-display-xs text-ink">Deployment</h3>
          <Button onClick={onTriggerTraining} disabled={busy}>
            Retrain candidate
          </Button>
        </div>
        {deployment ? (
          <dl className="gap-xl flex flex-wrap">
            <StatTile as="dl" label="Active" value={deployment.activeVersionLabel} emphasis="md" />
            <StatTile
              as="dl"
              label="Canary"
              value={
                deployment.canaryVersionLabel
                  ? `${deployment.canaryVersionLabel} @ ${deployment.canaryPercent}%`
                  : "—"
              }
              emphasis="md"
            />
            <StatTile
              as="dl"
              label="Previous"
              value={deployment.previousActiveVersionLabel ?? "—"}
              emphasis="md"
            />
          </dl>
        ) : (
          <p className="text-body-md text-body">No deployment is configured yet.</p>
        )}
        <div className="gap-sm flex flex-wrap">
          {deployment?.canaryVersionLabel ? (
            <Button variant="secondary" disabled={busy} onClick={onEvaluateCanary}>
              Evaluate canary
            </Button>
          ) : null}
          <Button variant="tertiary" disabled={busy} onClick={onRollback}>
            Roll back
          </Button>
        </div>
      </Card>

      <Card className="gap-md flex flex-col">
        <h3 className="text-display-xs text-ink">Model versions</h3>
        {versions.length === 0 ? (
          <EmptyState
            title="No model versions"
            description="Train a candidate to populate the registry."
          />
        ) : (
          <ul className="gap-md flex flex-col">
            {versions.map((version) => {
              const metric = metricText(version.metrics);
              return (
                <li
                  key={version.versionId}
                  className="gap-md border-canvas-soft pt-md flex flex-wrap items-center justify-between border-t first:border-0 first:pt-0"
                >
                  <div className="gap-xxs flex flex-col">
                    <span className="text-body-md text-ink font-semibold">
                      {version.versionLabel}
                    </span>
                    <span className="text-caption text-mute">
                      {metric ? `${metric} · ` : ""}
                      {formatDateTime(version.createdAt)}
                    </span>
                  </div>
                  <div className="gap-sm flex flex-wrap items-center">
                    <Badge tone="neutral">{humanize(version.status)}</Badge>
                    {versionActions(version)}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card className="gap-md flex flex-col">
        <h3 className="text-display-xs text-ink">Drift reports</h3>
        {driftReports.length === 0 ? (
          <EmptyState
            title="No drift reports"
            description="Advisory drift reports appear after a drift scan."
          />
        ) : (
          <ul className="gap-sm flex flex-col">
            {driftReports.map((report) => (
              <li key={report.driftReportId} className="gap-md flex flex-wrap items-center">
                <Badge tone={riskTone(report.severity)}>{humanize(report.severity)}</Badge>
                <span className="text-body-sm text-ink">{report.versionLabel}</span>
                <span className="text-caption text-mute">
                  {report.window} · advisory · {formatDateTime(report.createdAt)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
