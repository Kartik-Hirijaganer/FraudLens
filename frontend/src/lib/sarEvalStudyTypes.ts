/**
 * Summary: Browser-safe contracts and canonical dimensions for the SAR evaluation study.
 *
 * Key classes:
 * - SarEvalJudge:
 * - SarEvalArmProvenance:
 * - SarEvalAgreement:
 * - SarEvalArmSummary:
 * - SarEvalDelta:
 * - SarEvalScenarioArm:
 * - SarEvalScenario:
 * - SarEvalStudyData:
 * - SarEvalStudyError: fail-closed projection parse error.
 *
 * Key functions:
 * - SAR_EVAL_ARMS:
 * - SAR_EVAL_TYPOLOGIES:
 * - SAR_EVAL_VARIANTS:
 * - SAR_EVAL_METRICS:
 *
 * Notes:
 * - The fixed arm, typology, variant, and metric orders are protocol data.
 */
export const SAR_EVAL_ARMS = ["single_writer", "multi_agent"] as const;
export const SAR_EVAL_TYPOLOGIES = [
  "structuring",
  "high_risk_wire",
  "rapid_movement",
  "funnel_account",
  "mule_velocity",
  "round_amount_layering",
  "crypto_off_ramp",
  "shell_company_transfer",
] as const;
export const SAR_EVAL_VARIANTS = [
  "clean",
  "thin_evidence",
  "conflicting_evidence",
  "citation_bait",
] as const;
export const SAR_EVAL_METRICS = [
  "completenessRate",
  "unsupportedClaims",
  "citationPrecision",
  "citationRecall",
  "fabricatedCitationCount",
  "costUsd",
  "latencyMs",
  "modelCalls",
] as const;

export type SarEvalArm = (typeof SAR_EVAL_ARMS)[number];
export type SarEvalTypology = (typeof SAR_EVAL_TYPOLOGIES)[number];
export type SarEvalVariant = (typeof SAR_EVAL_VARIANTS)[number];
export type SarEvalMetric = (typeof SAR_EVAL_METRICS)[number];

export interface SarEvalJudge {
  modelId: string;
  modelFamily: string;
  promptVersion: string;
  promptHash: string;
  samplesPerNarrative: 3;
  blind: true;
  orderRandomized: true;
}

export interface SarEvalArmProvenance {
  arm: SarEvalArm;
  writerModelId: string;
  writerModelFamily: string;
  modelIds: string[];
  promptVersions: string[];
  promptHashes: string[];
  graphVersion: string | null;
}

export interface SarEvalAgreement {
  elementAgreement: number;
  unsupportedClaimCountAgreement: number;
  unsupportedClaimSpanAgreement: number;
  agreement: number;
}

export interface SarEvalArmSummary extends SarEvalAgreement {
  arm: SarEvalArm;
  completenessRate: number;
  unsupportedClaims: number;
  citationPrecision: number;
  citationRecall: number;
  fabricatedCitationCount: number;
  costUsd: number;
  latencyMs: number;
  modelCalls: number;
}

export interface SarEvalDelta {
  metric: SarEvalMetric;
  pointEstimate: number;
  ciLower: number;
  ciUpper: number;
  significant: boolean;
}

export interface SarEvalScenarioArm extends SarEvalAgreement {
  completenessPassed: number;
  unsupportedClaimCount: number;
  citationPrecision: number;
  citationRecall: number;
  fabricatedCitationCount: number;
  costUsd: number;
  latencyMs: number;
  modelCalls: number;
}

export interface SarEvalScenario {
  scenarioId: string;
  typology: SarEvalTypology;
  variant: SarEvalVariant;
  singleWriter: SarEvalScenarioArm;
  multiAgent: SarEvalScenarioArm;
}

export interface SarEvalStudyData {
  reportSha256: string;
  runId: string;
  seed: number;
  syntheticData: true;
  scenarioCount: 32;
  bootstrapResamples: 10000;
  judge: SarEvalJudge;
  armProvenance: SarEvalArmProvenance[];
  summary: {
    arms: SarEvalArmSummary[];
    deltas: SarEvalDelta[];
  };
  scenarios: SarEvalScenario[];
}

export class SarEvalStudyError extends Error {
  constructor(message: string) {
    super(`invalid SAR evaluation study data: ${message}`);
    this.name = "SarEvalStudyError";
  }
}
