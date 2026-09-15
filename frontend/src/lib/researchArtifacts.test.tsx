/**
 * Summary: Behavioral tests for strict research-artifact parsing and the three-view benchmark UI.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none)
 *
 * Notes:
 * - Fixtures contain aggregate synthetic values only and never stand in for published evidence.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import rawK8s from "../data/k8s-hpa-scaling.json";
import { InferenceBenchmark } from "../pages/InferenceBenchmark";
import {
  arrayOf,
  booleanOf,
  exactKeys,
  integerOf,
  nullableNumberOf,
  numberOf,
  recordOf,
  stringOf,
} from "./artifactGuards";
import { parseFullDataTrainingData } from "./fullDataTraining";
import { parseK8sHpaScalingData } from "./k8sHpaScaling";
import { parseVllmBenchmarkData } from "./vllmBenchmark";

const quality = {
  evaluated: 1020,
  schemaValidRate: 0.99,
  referenceValidity: 0.98,
  citationRecall: 0.97,
  requiredFactCoverage: 0.96,
  abstentionCorrectness: 1,
  fabricatedReferenceAttempts: 0,
  truncationRate: 0,
  unsupportedClaimFlags: 0,
  usefulCount: 990,
};

const telemetry = {
  samples: 12,
  gpuUtilizationMeanPct: 75,
  gpuUtilizationP95Pct: 92,
  memoryPeakMib: 21000,
  kvCachePeakPct: 48,
  queuePeak: 2,
};

function level(concurrency: number) {
  return {
    concurrency,
    requests: 1000,
    successful: 1000,
    retries: 0,
    errorRate: 0,
    latencyP50Ms: 100,
    latencyP95Ms: 200,
    latencyP99Ms: 250,
    ttftP50Ms: 20,
    ttftP95Ms: 40,
    requestsPerSecond: concurrency,
    generatedTokensPerSecond: concurrency * 100,
    usefulDraftsPerSecond: concurrency * 0.9,
    tokenAccountingDrift: 0,
    durationSeconds: 120,
    costUsd: 0.02,
    costPer1000DraftsUsd: 0.02,
    costUsdByPurchaseOption: { on_demand: 0.02 },
    costPer1000DraftsUsdByPurchaseOption: { on_demand: 0.02 },
    quality,
    telemetry,
  };
}

function server(arm: "bf16" | "awq") {
  return {
    arm,
    model: "org/model",
    modelRevision: "a".repeat(40),
    tokenizer: "org/tokenizer",
    tokenizerRevision: "b".repeat(40),
    image: "vllm/vllm-openai:v0.11.0",
    imageDigest: `sha256:${"c".repeat(64)}`,
    vllmVersion: "0.11.0",
    gpuName: "NVIDIA GeForce RTX 4090",
    driverVersion: "580.65",
    hostKey: "runpod_rtx4090_secure",
    provider: "RunPod",
    sku: "RTX 4090 Secure Cloud",
    region: "US",
    purchaseOption: "on_demand",
    hourlyRateUsd: 0.74,
    priceSourceUrl: "https://www.runpod.io/pricing",
    priceVerifiedAt: "2026-09-14",
    weightMemoryGib: arm === "bf16" ? 28 : 9,
    safetensorsTotalGib: arm === "bf16" ? 28 : 9,
    kvCacheTokens: arm === "bf16" ? 16000 : 48000,
    maximumConcurrency: 32,
  };
}

function vllmArtifact() {
  return {
    reportSha256: "d".repeat(64),
    runId: "vllm-bench-1234567890abcdef",
    headline: "AWQ reduced parsed model-weight memory by 67.9%; measured throughput shown below.",
    acceptanceMet: true,
    measuredCases: 1000,
    weightMemoryReduction: 0.679,
    arms: (["bf16", "awq"] as const).map((arm) => ({
      arm,
      server: server(arm),
      levels: [level(1), level(8), level(32)],
      quality,
      totalCostUsd: 0.06,
    })),
    qualityDeltas: [{ metric: "schema_valid_rate", deltaPercentagePoints: 0, warning: false }],
  };
}

function fullDataArtifact() {
  return {
    reportSha256: "e".repeat(64),
    runId: "full-data-run",
    provenance: "measured",
    applicationCandidate: "hi-medium",
    candidates: [
      {
        candidate: "hi-medium",
        source: "IBM-HI-Medium",
        sourceRows: 68200000,
        usableRows: 62000000,
        trainingRows: 43000000,
        evaluationRows: 9000000,
        prAuc: 0.82,
        baselinePrAuc: 0.74,
        recallAtBudget: 0.61,
        gatesPassed: true,
      },
    ],
  };
}

describe("research artifact parsers", () => {
  it("fails closed across every shared primitive artifact boundary", () => {
    expect(recordOf({ value: 1 }, "root")).toEqual({ value: 1 });
    for (const value of [null, [], "not-an-object"]) {
      expect(() => recordOf(value, "root")).toThrow(/must be an object/);
    }
    expect(() => exactKeys({ a: 1 }, ["a", "b"], "root")).toThrow(/fields/);
    expect(() => exactKeys({ a: 1 }, ["b"], "root")).toThrow(/fields/);
    exactKeys({ a: 1 }, ["a"], "root");
    expect(stringOf("value", "root")).toBe("value");
    expect(() => stringOf(1, "root")).toThrow(/non-empty string/);
    expect(() => stringOf("", "root")).toThrow(/non-empty string/);
    expect(numberOf(1.5, "root")).toBe(1.5);
    expect(() => numberOf("1", "root")).toThrow(/finite number/);
    expect(() => numberOf(Number.NaN, "root")).toThrow(/finite number/);
    expect(integerOf(2, "root")).toBe(2);
    expect(() => integerOf(2.5, "root")).toThrow(/integer/);
    expect(booleanOf(false, "root")).toBe(false);
    expect(() => booleanOf(0, "root")).toThrow(/boolean/);
    expect(arrayOf([1], "root")).toEqual([1]);
    expect(() => arrayOf({}, "root")).toThrow(/array/);
    expect(nullableNumberOf(null, "root")).toBeNull();
    expect(nullableNumberOf(3, "root")).toBe(3);
  });

  it("accepts the published Kubernetes evidence and rejects failed durability", () => {
    const parsed = parseK8sHpaScalingData(rawK8s);
    expect(parsed.summary.replicasMaxObserved).toBe(5);
    const corrupted = structuredClone(rawK8s);
    corrupted.durability.runs_completed = 99;
    expect(() => parseK8sHpaScalingData(corrupted)).toThrow(/durability gates/);
  });

  it("enforces the frozen vLLM arm order, levels, and measured case count", () => {
    const parsed = parseVllmBenchmarkData(vllmArtifact());
    expect(parsed.arms.map((arm) => arm.arm)).toEqual(["bf16", "awq"]);
    expect(parsed.arms[1].levels.map((item) => item.concurrency)).toEqual([1, 8, 32]);
    expect(() => parseVllmBenchmarkData({ ...vllmArtifact(), measuredCases: 999 })).toThrow(
      /must be 1000/,
    );
  });

  it("requires the full-data application candidate to be published", () => {
    expect(parseFullDataTrainingData(fullDataArtifact()).applicationCandidate).toBe("hi-medium");
    expect(() =>
      parseFullDataTrainingData({ ...fullDataArtifact(), applicationCandidate: "missing" }),
    ).toThrow(/published candidate/);
  });
});

describe("InferenceBenchmark", () => {
  it("switches across measured views and keeps table alternatives visible", async () => {
    render(
      <InferenceBenchmark
        inference={parseVllmBenchmarkData(vllmArtifact())}
        autoscaling={parseK8sHpaScalingData(rawK8s)}
        training={parseFullDataTrainingData(fullDataArtifact())}
      />,
    );
    expect(screen.getByTestId("study-finding")).toHaveTextContent(/weight memory/i);
    expect(screen.getByRole("table", { name: /latency, throughput/i })).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Autoscaling"));
    expect(screen.getByRole("img", { name: /replicas over elapsed/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /view chart data/i }));
    expect(screen.getByRole("table", { name: /replica and CPU/i })).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Training at scale"));
    expect(screen.getAllByText("68,200,000")).toHaveLength(2);
    expect(screen.getByText(/Source-row count is not the model-fitting count/)).toBeInTheDocument();
  });

  it("labels unavailable paid evidence as pending", async () => {
    render(
      <InferenceBenchmark
        inference={null}
        autoscaling={parseK8sHpaScalingData(rawK8s)}
        training={null}
      />,
    );
    expect(screen.getByText("Measured GPU benchmark pending")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Training at scale"));
    expect(screen.getByText("Measured full-data evidence pending")).toBeInTheDocument();
  });

  it("renders disclosed warning, unavailable telemetry, and failed-gate branches", async () => {
    const inference = parseVllmBenchmarkData(vllmArtifact());
    inference.acceptanceMet = false;
    inference.qualityDeltas[0].warning = true;
    inference.arms[0].levels[0].telemetry.gpuUtilizationMeanPct = null;
    inference.arms[0].levels[0].telemetry.kvCachePeakPct = null;
    const autoscaling = parseK8sHpaScalingData(rawK8s);
    autoscaling.summary.secondsToFirstScaleUp = null;
    autoscaling.summary.secondsToScaleBackToMin = null;
    autoscaling.durability.workerPodDeleted = false;
    const training = parseFullDataTrainingData(fullDataArtifact());
    training.candidates[0].gatesPassed = false;

    render(
      <InferenceBenchmark inference={inference} autoscaling={autoscaling} training={training} />,
    );
    expect(screen.getByText("Acceptance not met")).toBeInTheDocument();
    expect(screen.getByText(/0.00 pp · warning/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Autoscaling"));
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(screen.getAllByText("0s").length).toBeGreaterThanOrEqual(2);

    await userEvent.click(screen.getByLabelText("Training at scale"));
    expect(screen.getByText("Not passed")).toBeInTheDocument();
  });
});
