/**
 * Summary: Lazy route boundary binding committed, build-validated research artifacts to the
 * inference benchmark shell without a backend or cloud-provider request.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none) — the default export is route-only wiring.
 *
 * Notes:
 * - Missing paid evidence remains null; present evidence is strict-parsed during build.
 */
import { fullDataTrainingData } from "../data/fullDataTraining.data";
import { k8sHpaScalingData } from "../data/k8sHpaScaling.data";
import { vllmBenchmarkData } from "../data/vllmBenchmark.data";
import { InferenceBenchmark } from "./InferenceBenchmark";

export default function InferenceBenchmarkRoute() {
  return (
    <InferenceBenchmark
      inference={vllmBenchmarkData}
      autoscaling={k8sHpaScalingData}
      training={fullDataTrainingData}
    />
  );
}
