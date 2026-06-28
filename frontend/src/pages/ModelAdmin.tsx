/**
 * Summary: The admin model-lifecycle page (plan §5.4, §10.5, §16 Phase 11; FR-12). It
 * loads the registry versions, the live deployment pointer, and advisory drift reports,
 * and renders the ModelLifecyclePanel with handlers wired to the admin API: retrain a
 * candidate, promote candidate→shadow, approve, ramp the canary (5/25/50/100), evaluate
 * the canary auto-abort guard, and roll back. Each action toggles a busy guard, toasts the
 * outcome (PHI-free), and reloads — so the pointer flip is reflected with no redeploy.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ModelAdmin: render the model-lifecycle admin surface.
 *
 * Notes:
 * - A missing deployment (404) degrades to "none" rather than failing the page; admin-only
 * 403s surface as an "Admin only" toast via lib/errors.
 */
import { useCallback } from "react";

import { ModelLifecyclePanel } from "../components/ModelLifecyclePanel";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { PageHeader } from "../components/ui/PageHeader";
import {
  ApiError,
  apiClient,
  type ApiClient,
  type DeploymentResponse,
  type DriftReportListResponse,
  type ModelVersionListResponse,
} from "../lib/api";
import { notify } from "../lib/toast";
import { useAsync } from "../lib/useAsync";
import { useAsyncAction } from "../lib/useAsyncAction";

interface ModelAdminData {
  versions: ModelVersionListResponse;
  deployment: DeploymentResponse | null;
  drift: DriftReportListResponse;
}

interface ModelAdminProps {
  client?: ApiClient;
}

export function ModelAdmin({ client = apiClient }: ModelAdminProps) {
  const load = useCallback(async (): Promise<ModelAdminData> => {
    const [versions, deployment, drift] = await Promise.all([
      client.listModelVersions(),
      client.getDeployment().catch((caught: unknown): DeploymentResponse | null => {
        if (caught instanceof ApiError && caught.status === 404) {
          return null;
        }
        throw caught;
      }),
      client.listDriftReports(),
    ]);
    return { versions, deployment, drift };
  }, [client]);
  const state = useAsync(load, [client]);
  const { busy, run } = useAsyncAction(state.reload);

  const triggerTraining = (): Promise<void> =>
    run(async () => {
      const result = await client.triggerTraining();
      notify({
        tone: "positive",
        title: "Retrain submitted",
        description: `${result.labelTotal} matured labels available`,
      });
    });

  const evaluateCanary = (): Promise<void> =>
    run(async () => {
      const result = await client.evaluateCanary();
      notify({
        tone: result.aborted ? "warning" : "positive",
        title: result.aborted ? "Canary auto-aborted" : "Canary healthy",
        description: `deviation ${result.deviation.toFixed(3)}`,
      });
    });

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Model administration"
        description="Retrain, promote, and roll back the scoring model — human-gated, no redeploy."
      />
      <AsyncBoundary state={state}>
        {(data) => (
          <ModelLifecyclePanel
            versions={data.versions.versions}
            deployment={data.deployment}
            driftReports={data.drift.driftReports}
            busy={busy}
            onTriggerTraining={() => void triggerTraining()}
            onPromoteShadow={(versionId) =>
              void run(() => client.promoteToShadow(versionId), "Promoted to shadow")
            }
            onApprove={(versionId) =>
              void run(() => client.approveVersion(versionId), "Version approved")
            }
            onSetCanary={(versionId, percent) =>
              void run(
                () => client.setCanary(versionId, percent),
                percent === 100 ? "Promoted to active" : `Canary set to ${percent}%`,
              )
            }
            onRollback={() => void run(() => client.rollbackDeployment(), "Deployment rolled back")}
            onEvaluateCanary={() => void evaluateCanary()}
          />
        )}
      </AsyncBoundary>
    </section>
  );
}
