/**
 * Summary: Autoscaling view for the measured Kubernetes HPA and worker-recovery evidence.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AutoscalingBenchmark: render scaling metrics, an accessible step chart/table, and durability.
 *
 * Notes:
 * - kind is measured in release 0.3; the AKS row remains explicitly validation-only.
 */
import { DataTable, type Column } from "../ui/DataTable";
import { Disclosure } from "../ui/Disclosure";
import { StatTile } from "../ui/StatTile";
import type { K8sHpaScalingData, K8sScalingSample } from "../../lib/k8sHpaScaling";

interface AutoscalingBenchmarkProps {
  data: K8sHpaScalingData;
}

const SAMPLE_COLUMNS: Column<K8sScalingSample>[] = [
  { id: "time", header: "Elapsed", cell: (row) => `${row.elapsedSeconds}s` },
  { id: "replicas", header: "Replicas", cell: (row) => row.replicas, align: "right" },
  {
    id: "desired",
    header: "Desired",
    cell: (row) => row.desiredReplicas,
    align: "right",
  },
  {
    id: "cpu",
    header: "CPU / request",
    cell: (row) => (row.cpuPercent === null ? "—" : `${row.cpuPercent}%`),
    align: "right",
  },
];

function scalingPath(data: K8sHpaScalingData): string {
  const last = Math.max(...data.samples.map((sample) => sample.elapsedSeconds), 1);
  const max = Math.max(data.hpa.maxReplicas, 1);
  return data.samples
    .map((sample, index) => {
      const x = 8 + (sample.elapsedSeconds / last) * 284;
      const y = 92 - (sample.replicas / max) * 76;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

export function AutoscalingBenchmark({ data }: AutoscalingBenchmarkProps) {
  return (
    <div className="gap-xl flex flex-col">
      <div className="gap-md grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Replica range"
          value={`${data.summary.replicasMinObserved} → ${data.summary.replicasMaxObserved}`}
          hint={`${data.hpa.cpuTargetPercent}% CPU target`}
        />
        <StatTile
          label="First scale-out"
          value={`${data.summary.secondsToFirstScaleUp ?? 0}s`}
          hint="Observed under sustained load"
        />
        <StatTile
          label="Scale-down"
          value={`${data.summary.secondsToScaleBackToMin ?? 0}s`}
          hint="Returned to the configured minimum"
        />
        <StatTile
          label="Durable runs"
          value={`${data.durability.runsCompleted}/${data.durability.runsSubmitted}`}
          hint={`Recovered through attempt ${data.durability.maxRunAttempts}`}
        />
      </div>

      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Replicas over time</h2>
          <p className="text-body-sm text-body">
            HPA scaled the API under load and converged back to its one-replica floor.
          </p>
        </div>
        <svg viewBox="0 0 300 100" role="img" aria-labelledby="replica-chart-title">
          <title id="replica-chart-title">Observed API replicas over elapsed seconds</title>
          <line x1="8" y1="92" x2="292" y2="92" className="stroke-mute" />
          <path
            d={scalingPath(data)}
            fill="none"
            strokeWidth="4"
            strokeLinecap="round"
            className="stroke-negative"
          />
        </svg>
        <Disclosure summary="View chart data as a table">
          <DataTable
            rows={data.samples}
            columns={SAMPLE_COLUMNS}
            rowKey={(row) => String(row.elapsedSeconds)}
            caption="Kubernetes replica and CPU samples"
          />
        </Disclosure>
      </section>

      <section className="gap-lg bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Durability and platform boundary</h2>
        <dl className="gap-lg grid grid-cols-1 sm:grid-cols-3">
          <StatTile
            as="dl"
            label="Worker deleted"
            value={data.durability.workerPodDeleted ? "Yes" : "No"}
          />
          <StatTile
            as="dl"
            label="Completed after kill"
            value={data.durability.runsCompletedAfterWorkerKill}
          />
          <StatTile as="dl" label="Terminal failures" value={data.durability.runsFailed} />
        </dl>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="sr-only">Kubernetes platform evidence status</caption>
            <tbody className="text-body-sm">
              <tr className="border-canvas-soft border-t">
                <th className="py-md font-semibold">kind</th>
                <td className="py-md">Measured locally · {data.cluster.kubernetesVersion}</td>
              </tr>
              <tr className="border-canvas-soft border-t">
                <th className="py-md font-semibold">Azure AKS</th>
                <td className="py-md">Terraform and manifests validated; apply deferred</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul className="gap-xs text-caption text-body pl-xl flex list-disc flex-col">
          {data.disclosures.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
