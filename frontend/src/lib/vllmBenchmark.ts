/**
 * Summary: Strict browser contract and parser for the published BF16-versus-AWQ benchmark.
 *
 * Key classes:
 * - VllmQuality:
 * - VllmTelemetry:
 * - VllmServer: immutable model, image, GPU, host, and price provenance.
 * - VllmLevel: one concurrency level's latency, throughput, quality, telemetry, and cost.
 * - VllmArm: one precision arm and all three measured levels.
 * - VllmBenchmarkData: hash-bound aggregate benchmark projection.
 *
 * Key functions:
 * - parseVllmBenchmarkData: fail closed on malformed or protocol-drifted evidence.
 *
 * Notes:
 * - Raw prompts and model outputs never enter this aggregate frontend artifact.
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

export type VllmArmName = "bf16" | "awq";

export interface VllmQuality {
  evaluated: number;
  schemaValidRate: number;
  referenceValidity: number;
  citationRecall: number;
  requiredFactCoverage: number;
  abstentionCorrectness: number;
  fabricatedReferenceAttempts: number;
  truncationRate: number;
  unsupportedClaimFlags: number;
  usefulCount: number;
}

export interface VllmTelemetry {
  samples: number;
  gpuUtilizationMeanPct: number | null;
  gpuUtilizationP95Pct: number | null;
  memoryPeakMib: number | null;
  kvCachePeakPct: number | null;
  queuePeak: number | null;
}

export interface VllmServer {
  arm: VllmArmName;
  model: string;
  modelRevision: string;
  tokenizer: string;
  tokenizerRevision: string;
  image: string;
  imageDigest: string;
  vllmVersion: string;
  gpuName: string;
  driverVersion: string;
  hostKey: string;
  provider: string;
  sku: string;
  region: string;
  purchaseOption: string;
  hourlyRateUsd: number;
  priceSourceUrl: string;
  priceVerifiedAt: string;
  weightMemoryGib: number;
  safetensorsTotalGib: number;
  kvCacheTokens: number;
  maximumConcurrency: number;
}

export interface VllmLevel {
  concurrency: number;
  requests: number;
  successful: number;
  retries: number;
  errorRate: number;
  latencyP50Ms: number;
  latencyP95Ms: number;
  latencyP99Ms: number;
  ttftP50Ms: number;
  ttftP95Ms: number;
  requestsPerSecond: number;
  generatedTokensPerSecond: number;
  usefulDraftsPerSecond: number;
  tokenAccountingDrift: number;
  durationSeconds: number;
  costUsd: number;
  costPer1000DraftsUsd: number;
  costUsdByPurchaseOption: Record<string, number>;
  costPer1000DraftsUsdByPurchaseOption: Record<string, number>;
  quality: VllmQuality;
  telemetry: VllmTelemetry;
}

export interface VllmArm {
  arm: VllmArmName;
  server: VllmServer;
  levels: VllmLevel[];
  quality: VllmQuality;
  totalCostUsd: number;
}

export interface VllmBenchmarkData {
  reportSha256: string;
  runId: string;
  headline: string;
  acceptanceMet: boolean;
  measuredCases: number;
  weightMemoryReduction: number;
  arms: [VllmArm, VllmArm];
  qualityDeltas: Array<{ metric: string; deltaPercentagePoints: number; warning: boolean }>;
}

const QUALITY_KEYS = [
  "evaluated",
  "schemaValidRate",
  "referenceValidity",
  "citationRecall",
  "requiredFactCoverage",
  "abstentionCorrectness",
  "fabricatedReferenceAttempts",
  "truncationRate",
  "unsupportedClaimFlags",
  "usefulCount",
];
const TELEMETRY_KEYS = [
  "samples",
  "gpuUtilizationMeanPct",
  "gpuUtilizationP95Pct",
  "memoryPeakMib",
  "kvCachePeakPct",
  "queuePeak",
];
const SERVER_KEYS = [
  "arm",
  "model",
  "modelRevision",
  "tokenizer",
  "tokenizerRevision",
  "image",
  "imageDigest",
  "vllmVersion",
  "gpuName",
  "driverVersion",
  "hostKey",
  "provider",
  "sku",
  "region",
  "purchaseOption",
  "hourlyRateUsd",
  "priceSourceUrl",
  "priceVerifiedAt",
  "weightMemoryGib",
  "safetensorsTotalGib",
  "kvCacheTokens",
  "maximumConcurrency",
];
const LEVEL_NUMBER_KEYS = [
  "latencyP50Ms",
  "latencyP95Ms",
  "latencyP99Ms",
  "ttftP50Ms",
  "ttftP95Ms",
  "requestsPerSecond",
  "generatedTokensPerSecond",
  "usefulDraftsPerSecond",
  "tokenAccountingDrift",
  "durationSeconds",
  "costUsd",
  "costPer1000DraftsUsd",
] as const;
const LEVEL_KEYS = [
  "concurrency",
  "requests",
  "successful",
  "retries",
  "errorRate",
  ...LEVEL_NUMBER_KEYS,
  "costUsdByPurchaseOption",
  "costPer1000DraftsUsdByPurchaseOption",
  "quality",
  "telemetry",
];

function armNameOf(value: unknown, path: string): VllmArmName {
  if (value !== "bf16" && value !== "awq") throw new ArtifactError(`${path} is invalid`);
  return value;
}

function rateOf(value: unknown, path: string): number {
  const parsed = numberOf(value, path);
  if (parsed < 0 || parsed > 1) throw new ArtifactError(`${path} must be within [0, 1]`);
  return parsed;
}

function qualityOf(value: unknown, path: string): VllmQuality {
  const raw = recordOf(value, path);
  exactKeys(raw, QUALITY_KEYS, path);
  return {
    evaluated: integerOf(raw.evaluated, `${path}.evaluated`),
    schemaValidRate: rateOf(raw.schemaValidRate, `${path}.schemaValidRate`),
    referenceValidity: rateOf(raw.referenceValidity, `${path}.referenceValidity`),
    citationRecall: rateOf(raw.citationRecall, `${path}.citationRecall`),
    requiredFactCoverage: rateOf(raw.requiredFactCoverage, `${path}.requiredFactCoverage`),
    abstentionCorrectness: rateOf(raw.abstentionCorrectness, `${path}.abstentionCorrectness`),
    fabricatedReferenceAttempts: integerOf(
      raw.fabricatedReferenceAttempts,
      `${path}.fabricatedReferenceAttempts`,
    ),
    truncationRate: rateOf(raw.truncationRate, `${path}.truncationRate`),
    unsupportedClaimFlags: integerOf(raw.unsupportedClaimFlags, `${path}.unsupportedClaimFlags`),
    usefulCount: integerOf(raw.usefulCount, `${path}.usefulCount`),
  };
}

function telemetryOf(value: unknown, path: string): VllmTelemetry {
  const raw = recordOf(value, path);
  exactKeys(raw, TELEMETRY_KEYS, path);
  return {
    samples: integerOf(raw.samples, `${path}.samples`),
    gpuUtilizationMeanPct: nullableNumberOf(
      raw.gpuUtilizationMeanPct,
      `${path}.gpuUtilizationMeanPct`,
    ),
    gpuUtilizationP95Pct: nullableNumberOf(
      raw.gpuUtilizationP95Pct,
      `${path}.gpuUtilizationP95Pct`,
    ),
    memoryPeakMib: nullableNumberOf(raw.memoryPeakMib, `${path}.memoryPeakMib`),
    kvCachePeakPct: nullableNumberOf(raw.kvCachePeakPct, `${path}.kvCachePeakPct`),
    queuePeak: nullableNumberOf(raw.queuePeak, `${path}.queuePeak`),
  };
}

function numericMapOf(value: unknown, path: string): Record<string, number> {
  const raw = recordOf(value, path);
  if (Object.keys(raw).length === 0) throw new ArtifactError(`${path} must not be empty`);
  return Object.fromEntries(
    Object.entries(raw).map(([key, item]) => [key, numberOf(item, `${path}.${key}`)]),
  );
}

function serverOf(value: unknown, path: string): VllmServer {
  const raw = recordOf(value, path);
  exactKeys(raw, SERVER_KEYS, path);
  const stringKeys = SERVER_KEYS.filter(
    (key) =>
      ![
        "arm",
        "hourlyRateUsd",
        "weightMemoryGib",
        "safetensorsTotalGib",
        "kvCacheTokens",
        "maximumConcurrency",
      ].includes(key),
  );
  const strings = Object.fromEntries(
    stringKeys.map((key) => [key, stringOf(raw[key], `${path}.${key}`)]),
  ) as Record<string, string>;
  const imageDigest = strings.imageDigest;
  if (!/^sha256:[0-9a-f]{64}$/.test(imageDigest)) throw new ArtifactError("invalid image digest");
  return {
    ...(strings as Omit<
      VllmServer,
      | "arm"
      | "hourlyRateUsd"
      | "weightMemoryGib"
      | "safetensorsTotalGib"
      | "kvCacheTokens"
      | "maximumConcurrency"
    >),
    arm: armNameOf(raw.arm, `${path}.arm`),
    hourlyRateUsd: numberOf(raw.hourlyRateUsd, `${path}.hourlyRateUsd`),
    weightMemoryGib: numberOf(raw.weightMemoryGib, `${path}.weightMemoryGib`),
    safetensorsTotalGib: numberOf(raw.safetensorsTotalGib, `${path}.safetensorsTotalGib`),
    kvCacheTokens: integerOf(raw.kvCacheTokens, `${path}.kvCacheTokens`),
    maximumConcurrency: numberOf(raw.maximumConcurrency, `${path}.maximumConcurrency`),
  };
}

function levelOf(value: unknown, path: string): VllmLevel {
  const raw = recordOf(value, path);
  exactKeys(raw, LEVEL_KEYS, path);
  const metrics = Object.fromEntries(
    LEVEL_NUMBER_KEYS.map((key) => [key, numberOf(raw[key], `${path}.${key}`)]),
  ) as Pick<VllmLevel, (typeof LEVEL_NUMBER_KEYS)[number]>;
  return {
    concurrency: integerOf(raw.concurrency, `${path}.concurrency`),
    requests: integerOf(raw.requests, `${path}.requests`),
    successful: integerOf(raw.successful, `${path}.successful`),
    retries: integerOf(raw.retries, `${path}.retries`),
    errorRate: rateOf(raw.errorRate, `${path}.errorRate`),
    ...metrics,
    costUsdByPurchaseOption: numericMapOf(
      raw.costUsdByPurchaseOption,
      `${path}.costUsdByPurchaseOption`,
    ),
    costPer1000DraftsUsdByPurchaseOption: numericMapOf(
      raw.costPer1000DraftsUsdByPurchaseOption,
      `${path}.costPer1000DraftsUsdByPurchaseOption`,
    ),
    quality: qualityOf(raw.quality, `${path}.quality`),
    telemetry: telemetryOf(raw.telemetry, `${path}.telemetry`),
  };
}

function armOf(value: unknown, index: number): VllmArm {
  const path = `arms[${index}]`;
  const raw = recordOf(value, path);
  exactKeys(raw, ["arm", "server", "levels", "quality", "totalCostUsd"], path);
  const arm = armNameOf(raw.arm, `${path}.arm`);
  const server = serverOf(raw.server, `${path}.server`);
  const levels = arrayOf(raw.levels, `${path}.levels`).map((item, levelIndex) =>
    levelOf(item, `${path}.levels[${levelIndex}]`),
  );
  if (server.arm !== arm || levels.map((item) => item.concurrency).join(",") !== "1,8,32") {
    throw new ArtifactError(`${path} does not match the frozen arm/concurrency protocol`);
  }
  return {
    arm,
    server,
    levels,
    quality: qualityOf(raw.quality, `${path}.quality`),
    totalCostUsd: numberOf(raw.totalCostUsd, `${path}.totalCostUsd`),
  };
}

export function parseVllmBenchmarkData(value: unknown): VllmBenchmarkData {
  const raw = recordOf(value, "vLLM artifact");
  exactKeys(
    raw,
    [
      "reportSha256",
      "runId",
      "headline",
      "acceptanceMet",
      "measuredCases",
      "weightMemoryReduction",
      "arms",
      "qualityDeltas",
    ],
    "vLLM artifact",
  );
  const reportSha256 = stringOf(raw.reportSha256, "reportSha256");
  if (!/^[0-9a-f]{64}$/.test(reportSha256)) throw new ArtifactError("invalid reportSha256");
  const measuredCases = integerOf(raw.measuredCases, "measuredCases");
  if (measuredCases !== 1000) throw new ArtifactError("measuredCases must be 1000");
  const parsedArms = arrayOf(raw.arms, "arms").map(armOf);
  if (parsedArms.length !== 2 || parsedArms[0].arm !== "bf16" || parsedArms[1].arm !== "awq") {
    throw new ArtifactError("arms must be ordered BF16 then AWQ");
  }
  const qualityDeltas = arrayOf(raw.qualityDeltas, "qualityDeltas").map((value, index) => {
    const path = `qualityDeltas[${index}]`;
    const item = recordOf(value, path);
    exactKeys(item, ["metric", "deltaPercentagePoints", "warning"], path);
    return {
      metric: stringOf(item.metric, `${path}.metric`),
      deltaPercentagePoints: numberOf(item.deltaPercentagePoints, `${path}.deltaPercentagePoints`),
      warning: booleanOf(item.warning, `${path}.warning`),
    };
  });
  return {
    reportSha256,
    runId: stringOf(raw.runId, "runId"),
    headline: stringOf(raw.headline, "headline"),
    acceptanceMet: booleanOf(raw.acceptanceMet, "acceptanceMet"),
    measuredCases,
    weightMemoryReduction: rateOf(raw.weightMemoryReduction, "weightMemoryReduction"),
    arms: parsedArms as [VllmArm, VllmArm],
    qualityDeltas,
  };
}
