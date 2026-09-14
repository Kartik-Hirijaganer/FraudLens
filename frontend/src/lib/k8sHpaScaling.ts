/**
 * Summary: Strict parser for the published Kubernetes HPA and durable-worker evidence artifact.
 *
 * Key classes:
 * - K8sScalingSample: one elapsed-time replica and CPU observation.
 * - K8sHpaScalingData: validated scaling, workload, disclosure, and recovery evidence.
 *
 * Key functions:
 * - parseK8sHpaScalingData: reject malformed or incomplete Kubernetes evidence.
 *
 * Notes:
 * - The parser consumes the publisher's snake_case JSON and exposes camelCase UI fields.
 */
import {
  arrayOf,
  ArtifactError,
  booleanOf,
  exactKeys,
  integerOf,
  nullableNumberOf,
  numberOf,
  recordOf,
  stringOf,
} from "./artifactGuards";

export interface K8sScalingSample {
  elapsedSeconds: number;
  replicas: number;
  desiredReplicas: number;
  cpuPercent: number | null;
}

export interface K8sHpaScalingData {
  schemaVersion: string;
  generatedAt: string;
  platform: "kind" | "aks";
  commit: string;
  cluster: { name: string; context: string; kubernetesVersion: string; nodeCount: number };
  hpa: { minReplicas: number; maxReplicas: number; cpuTargetPercent: number };
  samples: K8sScalingSample[];
  summary: {
    replicasMinObserved: number;
    replicasMaxObserved: number;
    secondsToFirstScaleUp: number | null;
    secondsToMaxReplicas: number | null;
    secondsToScaleBackToMin: number | null;
  };
  durability: {
    workerPodDeleted: boolean;
    runsSubmitted: number;
    runsCompleted: number;
    runsCompletedAfterWorkerKill: number;
    runsFailed: number;
    maxRunAttempts: number;
  };
  load: { requests: number; succeeded: number; failed: number; durationSeconds: number };
  disclosures: string[];
}

function platformOf(value: unknown): "kind" | "aks" {
  if (value !== "kind" && value !== "aks") throw new ArtifactError("invalid platform");
  return value;
}

export function parseK8sHpaScalingData(value: unknown): K8sHpaScalingData {
  const raw = recordOf(value, "Kubernetes artifact");
  exactKeys(
    raw,
    [
      "schema_version",
      "generated_at",
      "platform",
      "commit",
      "config_sha256",
      "cluster",
      "hpa",
      "workload",
      "load",
      "samples",
      "summary",
      "durability",
      "disclosures",
    ],
    "Kubernetes artifact",
  );
  const cluster = recordOf(raw.cluster, "cluster");
  exactKeys(
    cluster,
    ["name", "context", "kubernetes_version", "node_count", "architectures"],
    "cluster",
  );
  const hpa = recordOf(raw.hpa, "hpa");
  exactKeys(
    hpa,
    [
      "target",
      "min_replicas",
      "max_replicas",
      "cpu_target_percent",
      "scale_down_stabilization_seconds",
    ],
    "hpa",
  );
  const summary = recordOf(raw.summary, "summary");
  exactKeys(
    summary,
    [
      "replicas_min_observed",
      "replicas_max_observed",
      "seconds_to_first_scale_up",
      "seconds_to_max_replicas",
      "seconds_to_scale_back_to_min",
    ],
    "summary",
  );
  const durability = recordOf(raw.durability, "durability");
  exactKeys(
    durability,
    [
      "worker_pod_deleted",
      "runs_submitted",
      "runs_completed",
      "runs_completed_after_worker_kill",
      "runs_failed",
      "max_run_attempts",
    ],
    "durability",
  );
  const load = recordOf(raw.load, "load");
  exactKeys(
    load,
    [
      "mode",
      "requests",
      "succeeded",
      "failed",
      "duration_seconds",
      "latency_p50_ms",
      "latency_p95_ms",
      "runs_submitted",
      "runs_completed",
      "runs_failed",
      "max_run_attempts",
    ],
    "load",
  );
  const samples = arrayOf(raw.samples, "samples").map((value, index) => {
    const path = `samples[${index}]`;
    const item = recordOf(value, path);
    exactKeys(item, ["elapsed_seconds", "replicas", "desired_replicas", "cpu_percent"], path);
    return {
      elapsedSeconds: integerOf(item.elapsed_seconds, `${path}.elapsed_seconds`),
      replicas: integerOf(item.replicas, `${path}.replicas`),
      desiredReplicas: integerOf(item.desired_replicas, `${path}.desired_replicas`),
      cpuPercent: nullableNumberOf(item.cpu_percent, `${path}.cpu_percent`),
    };
  });
  if (samples.length < 2) throw new ArtifactError("samples must include a scaling time series");
  const minReplicas = integerOf(hpa.min_replicas, "hpa.min_replicas");
  const maxReplicas = integerOf(hpa.max_replicas, "hpa.max_replicas");
  const replicasMinObserved = integerOf(
    summary.replicas_min_observed,
    "summary.replicas_min_observed",
  );
  const replicasMaxObserved = integerOf(
    summary.replicas_max_observed,
    "summary.replicas_max_observed",
  );
  const runsSubmitted = integerOf(durability.runs_submitted, "durability.runs_submitted");
  const runsCompleted = integerOf(durability.runs_completed, "durability.runs_completed");
  if (
    replicasMinObserved !== minReplicas ||
    replicasMaxObserved < maxReplicas ||
    runsSubmitted === 0 ||
    runsCompleted !== runsSubmitted
  ) {
    throw new ArtifactError("Kubernetes evidence does not satisfy scaling and durability gates");
  }
  const disclosures = arrayOf(raw.disclosures, "disclosures").map((item, index) =>
    stringOf(item, `disclosures[${index}]`),
  );
  return {
    schemaVersion: stringOf(raw.schema_version, "schema_version"),
    generatedAt: stringOf(raw.generated_at, "generated_at"),
    platform: platformOf(raw.platform),
    commit: stringOf(raw.commit, "commit"),
    cluster: {
      name: stringOf(cluster.name, "cluster.name"),
      context: stringOf(cluster.context, "cluster.context"),
      kubernetesVersion: stringOf(cluster.kubernetes_version, "cluster.kubernetes_version"),
      nodeCount: integerOf(cluster.node_count, "cluster.node_count"),
    },
    hpa: {
      minReplicas,
      maxReplicas,
      cpuTargetPercent: integerOf(hpa.cpu_target_percent, "hpa.cpu_target_percent"),
    },
    samples,
    summary: {
      replicasMinObserved,
      replicasMaxObserved,
      secondsToFirstScaleUp: nullableNumberOf(
        summary.seconds_to_first_scale_up,
        "summary.seconds_to_first_scale_up",
      ),
      secondsToMaxReplicas: nullableNumberOf(
        summary.seconds_to_max_replicas,
        "summary.seconds_to_max_replicas",
      ),
      secondsToScaleBackToMin: nullableNumberOf(
        summary.seconds_to_scale_back_to_min,
        "summary.seconds_to_scale_back_to_min",
      ),
    },
    durability: {
      workerPodDeleted: booleanOf(durability.worker_pod_deleted, "durability.worker_pod_deleted"),
      runsSubmitted,
      runsCompleted,
      runsCompletedAfterWorkerKill: integerOf(
        durability.runs_completed_after_worker_kill,
        "durability.runs_completed_after_worker_kill",
      ),
      runsFailed: integerOf(durability.runs_failed, "durability.runs_failed"),
      maxRunAttempts: integerOf(durability.max_run_attempts, "durability.max_run_attempts"),
    },
    load: {
      requests: integerOf(load.requests, "load.requests"),
      succeeded: integerOf(load.succeeded, "load.succeeded"),
      failed: integerOf(load.failed, "load.failed"),
      durationSeconds: numberOf(load.duration_seconds, "load.duration_seconds"),
    },
    disclosures,
  };
}
