/**
 * Summary: Shared research shell for inference, Kubernetes autoscaling, and full-data training
 * evidence. A keyboard-accessible segmented control switches views without changing provenance.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - InferenceBenchmark: render all three validated research projections in one lazy page.
 *
 * Notes:
 * - Missing paid-experiment artifacts render an honest pending state, never sample measurements.
 */
import { useState } from "react";

import { AutoscalingBenchmark } from "../components/benchmark/AutoscalingBenchmark";
import { InferenceResults } from "../components/benchmark/InferenceResults";
import { TrainingBenchmark } from "../components/benchmark/TrainingBenchmark";
import { EmptyState } from "../components/feedback/EmptyState";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import type { FullDataTrainingData } from "../lib/fullDataTraining";
import type { K8sHpaScalingData } from "../lib/k8sHpaScaling";
import type { VllmBenchmarkData } from "../lib/vllmBenchmark";

type BenchmarkView = "inference" | "autoscaling" | "training";

interface InferenceBenchmarkProps {
  inference: VllmBenchmarkData | null;
  autoscaling: K8sHpaScalingData;
  training: FullDataTrainingData | null;
}

const VIEWS = [
  { value: "inference", label: "Inference" },
  { value: "autoscaling", label: "Autoscaling" },
  { value: "training", label: "Training at scale" },
] as const;

export function InferenceBenchmark({ inference, autoscaling, training }: InferenceBenchmarkProps) {
  const [view, setView] = useState<BenchmarkView>("inference");
  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Inference benchmark"
        description="Measured performance, Kubernetes scaling, and full-data model evidence—with protocol boundaries kept visible."
        actions={
          <SegmentedControl
            options={VIEWS}
            value={view}
            onChange={(value) => setView(value as BenchmarkView)}
            ariaLabel="Benchmark evidence view"
          />
        }
      />
      <div aria-live="polite">
        {view === "inference" ? (
          inference ? (
            <InferenceResults data={inference} />
          ) : (
            <EmptyState
              title="Measured GPU benchmark pending"
              description="No RunPod or Azure GPU has been created. Results will appear only after provider approval, the frozen benchmark, validation, and teardown."
            />
          )
        ) : view === "autoscaling" ? (
          <AutoscalingBenchmark data={autoscaling} />
        ) : (
          <TrainingBenchmark data={training} />
        )}
      </div>
    </section>
  );
}
