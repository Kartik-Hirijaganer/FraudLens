/**
 * Summary: Primitive and structural guards for the committed SAR study projection.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - EXPECTED_SCENARIO_COUNT:
 * - EXPECTED_BOOTSTRAP_RESAMPLES:
 * - asRecord:
 * - assertKeys:
 * - asString:
 * - asHash:
 * - asInteger:
 * - familyFromModelRef:
 * - parseJudge: validate judge provenance.
 * - parseArmProvenance: validate writer provenance.
 * - parseArmSummary:
 * - parseDelta:
 * - parseScenario: validate one paired scenario.
 * - assertCompleteArms:
 * - assertCompleteDeltas:
 * - assertScenarioMatrix: enforce the fixed 8-by-4 matrix.
 *
 * Notes:
 * - Every helper throws SarEvalStudyError and accepts unknown input.
 */
import {
  SAR_EVAL_ARMS,
  SAR_EVAL_METRICS,
  SAR_EVAL_TYPOLOGIES,
  SAR_EVAL_VARIANTS,
  SarEvalStudyError,
  type SarEvalAgreement,
  type SarEvalArm,
  type SarEvalArmProvenance,
  type SarEvalArmSummary,
  type SarEvalDelta,
  type SarEvalJudge,
  type SarEvalScenario,
  type SarEvalScenarioArm,
} from "./sarEvalStudyTypes";

const HASH_PATTERN = /^[0-9a-f]{64}$/;
export const EXPECTED_SCENARIO_COUNT = 32;
export const EXPECTED_BOOTSTRAP_RESAMPLES = 10_000;
const EXPECTED_JUDGE_SAMPLES = 3;
const FINCEN_ELEMENT_COUNT = 5;
const AGREEMENT_MEAN_TOLERANCE = 1e-9;

export function asRecord(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new SarEvalStudyError(`${where} must be an object`);
  }
  return value as Record<string, unknown>;
}

export function assertKeys(
  raw: Record<string, unknown>,
  expected: readonly string[],
  where: string,
): void {
  const actual = Object.keys(raw).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new SarEvalStudyError(`${where} keys must be exactly ${wanted.join(", ")}`);
  }
}

export function asString(value: unknown, where: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new SarEvalStudyError(`${where} must be a non-empty string`);
  }
  return value;
}

export function asHash(value: unknown, where: string): string {
  const hash = asString(value, where);
  if (!HASH_PATTERN.test(hash)) {
    throw new SarEvalStudyError(`${where} must be a 64-character lowercase hex digest`);
  }
  return hash;
}

function asFiniteNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new SarEvalStudyError(`${where} must be a finite number`);
  }
  return value;
}

function asNonNegative(value: unknown, where: string): number {
  const number = asFiniteNumber(value, where);
  if (number < 0) {
    throw new SarEvalStudyError(`${where} must be non-negative`);
  }
  return number;
}

function asPositive(value: unknown, where: string): number {
  const number = asFiniteNumber(value, where);
  if (number <= 0) {
    throw new SarEvalStudyError(`${where} must be positive`);
  }
  return number;
}

export function asInteger(value: unknown, where: string, minimum = 0): number {
  const number = asFiniteNumber(value, where);
  if (!Number.isInteger(number) || number < minimum) {
    throw new SarEvalStudyError(`${where} must be an integer >= ${minimum}`);
  }
  return number;
}

function asRate(value: unknown, where: string): number {
  const rate = asFiniteNumber(value, where);
  if (rate < 0 || rate > 1) {
    throw new SarEvalStudyError(`${where} must be in [0, 1]`);
  }
  return rate;
}

function parseAgreement(raw: Record<string, unknown>, where: string): SarEvalAgreement {
  const elementAgreement = asRate(raw.elementAgreement, `${where}.elementAgreement`);
  const unsupportedClaimCountAgreement = asRate(
    raw.unsupportedClaimCountAgreement,
    `${where}.unsupportedClaimCountAgreement`,
  );
  const unsupportedClaimSpanAgreement = asRate(
    raw.unsupportedClaimSpanAgreement,
    `${where}.unsupportedClaimSpanAgreement`,
  );
  const agreement = asRate(raw.agreement, `${where}.agreement`);
  const componentMean =
    (elementAgreement + unsupportedClaimCountAgreement + unsupportedClaimSpanAgreement) / 3;
  if (Math.abs(agreement - componentMean) > AGREEMENT_MEAN_TOLERANCE) {
    throw new SarEvalStudyError(`${where}.agreement must equal the mean of its component rates`);
  }
  return {
    elementAgreement,
    unsupportedClaimCountAgreement,
    unsupportedClaimSpanAgreement,
    agreement,
  };
}

function asLiteral<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
  const literal = asString(value, where);
  if (!(allowed as readonly string[]).includes(literal)) {
    throw new SarEvalStudyError(`${where} must be one of ${allowed.join(", ")}`);
  }
  return literal as T;
}

function asStringArray(value: unknown, where: string, hashes = false): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new SarEvalStudyError(`${where} must be a non-empty array`);
  }
  const values = value.map((item, index) =>
    hashes ? asHash(item, `${where}[${index}]`) : asString(item, `${where}[${index}]`),
  );
  if (new Set(values).size !== values.length) {
    throw new SarEvalStudyError(`${where} must not contain duplicates`);
  }
  return values;
}

export function familyFromModelRef(modelId: string, where: string): string {
  const segments = modelId.split("/");
  if (segments.length < 3 || segments.some((segment) => segment.length === 0)) {
    throw new SarEvalStudyError(`${where} must be a router/family/model reference`);
  }
  return segments[1];
}

export function parseJudge(value: unknown): SarEvalJudge {
  const raw = asRecord(value, "judge");
  assertKeys(
    raw,
    [
      "modelId",
      "modelFamily",
      "promptVersion",
      "promptHash",
      "samplesPerNarrative",
      "blind",
      "orderRandomized",
    ],
    "judge",
  );
  if (raw.samplesPerNarrative !== EXPECTED_JUDGE_SAMPLES) {
    throw new SarEvalStudyError(`judge.samplesPerNarrative must be ${EXPECTED_JUDGE_SAMPLES}`);
  }
  if (raw.blind !== true || raw.orderRandomized !== true) {
    throw new SarEvalStudyError("judge must be blind and order-randomized");
  }
  return {
    modelId: asString(raw.modelId, "judge.modelId"),
    modelFamily: asString(raw.modelFamily, "judge.modelFamily"),
    promptVersion: asString(raw.promptVersion, "judge.promptVersion"),
    promptHash: asHash(raw.promptHash, "judge.promptHash"),
    samplesPerNarrative: EXPECTED_JUDGE_SAMPLES,
    blind: true,
    orderRandomized: true,
  };
}

export function parseArmProvenance(value: unknown, index: number): SarEvalArmProvenance {
  const where = `armProvenance[${index}]`;
  const raw = asRecord(value, where);
  assertKeys(
    raw,
    [
      "arm",
      "writerModelId",
      "writerModelFamily",
      "modelIds",
      "promptVersions",
      "promptHashes",
      "graphVersion",
    ],
    where,
  );
  return {
    arm: asLiteral(raw.arm, SAR_EVAL_ARMS, `${where}.arm`),
    writerModelId: asString(raw.writerModelId, `${where}.writerModelId`),
    writerModelFamily: asString(raw.writerModelFamily, `${where}.writerModelFamily`),
    modelIds: asStringArray(raw.modelIds, `${where}.modelIds`),
    promptVersions: asStringArray(raw.promptVersions, `${where}.promptVersions`),
    promptHashes: asStringArray(raw.promptHashes, `${where}.promptHashes`, true),
    graphVersion:
      raw.graphVersion === null ? null : asString(raw.graphVersion, `${where}.graphVersion`),
  };
}

export function parseArmSummary(value: unknown, index: number): SarEvalArmSummary {
  const where = `summary.arms[${index}]`;
  const raw = asRecord(value, where);
  assertKeys(
    raw,
    [
      "arm",
      "completenessRate",
      "unsupportedClaims",
      "citationPrecision",
      "citationRecall",
      "fabricatedCitationCount",
      "costUsd",
      "latencyMs",
      "modelCalls",
      "elementAgreement",
      "unsupportedClaimCountAgreement",
      "unsupportedClaimSpanAgreement",
      "agreement",
    ],
    where,
  );
  return {
    arm: asLiteral(raw.arm, SAR_EVAL_ARMS, `${where}.arm`),
    completenessRate: asRate(raw.completenessRate, `${where}.completenessRate`),
    unsupportedClaims: asNonNegative(raw.unsupportedClaims, `${where}.unsupportedClaims`),
    citationPrecision: asRate(raw.citationPrecision, `${where}.citationPrecision`),
    citationRecall: asRate(raw.citationRecall, `${where}.citationRecall`),
    fabricatedCitationCount: asNonNegative(
      raw.fabricatedCitationCount,
      `${where}.fabricatedCitationCount`,
    ),
    costUsd: asNonNegative(raw.costUsd, `${where}.costUsd`),
    latencyMs: asNonNegative(raw.latencyMs, `${where}.latencyMs`),
    modelCalls: asPositive(raw.modelCalls, `${where}.modelCalls`),
    ...parseAgreement(raw, where),
  };
}

export function parseDelta(value: unknown, index: number): SarEvalDelta {
  const where = `summary.deltas[${index}]`;
  const raw = asRecord(value, where);
  assertKeys(raw, ["metric", "pointEstimate", "ciLower", "ciUpper", "significant"], where);
  const ciLower = asFiniteNumber(raw.ciLower, `${where}.ciLower`);
  const ciUpper = asFiniteNumber(raw.ciUpper, `${where}.ciUpper`);
  if (ciLower > ciUpper) {
    throw new SarEvalStudyError(`${where} interval lower bound exceeds its upper bound`);
  }
  const derivedSignificance = ciLower > 0 || ciUpper < 0;
  if (raw.significant !== derivedSignificance) {
    throw new SarEvalStudyError(`${where}.significant must equal interval exclusion of zero`);
  }
  return {
    metric: asLiteral(raw.metric, SAR_EVAL_METRICS, `${where}.metric`),
    pointEstimate: asFiniteNumber(raw.pointEstimate, `${where}.pointEstimate`),
    ciLower,
    ciUpper,
    significant: derivedSignificance,
  };
}

function parseScenarioArm(value: unknown, where: string): SarEvalScenarioArm {
  const raw = asRecord(value, where);
  assertKeys(
    raw,
    [
      "completenessPassed",
      "unsupportedClaimCount",
      "citationPrecision",
      "citationRecall",
      "fabricatedCitationCount",
      "costUsd",
      "latencyMs",
      "modelCalls",
      "elementAgreement",
      "unsupportedClaimCountAgreement",
      "unsupportedClaimSpanAgreement",
      "agreement",
    ],
    where,
  );
  const completenessPassed = asInteger(raw.completenessPassed, `${where}.completenessPassed`);
  if (completenessPassed > FINCEN_ELEMENT_COUNT) {
    throw new SarEvalStudyError(`${where}.completenessPassed must be in [0, 5]`);
  }
  return {
    completenessPassed,
    unsupportedClaimCount: asInteger(raw.unsupportedClaimCount, `${where}.unsupportedClaimCount`),
    citationPrecision: asRate(raw.citationPrecision, `${where}.citationPrecision`),
    citationRecall: asRate(raw.citationRecall, `${where}.citationRecall`),
    fabricatedCitationCount: asInteger(
      raw.fabricatedCitationCount,
      `${where}.fabricatedCitationCount`,
    ),
    costUsd: asNonNegative(raw.costUsd, `${where}.costUsd`),
    latencyMs: asNonNegative(raw.latencyMs, `${where}.latencyMs`),
    modelCalls: asInteger(raw.modelCalls, `${where}.modelCalls`, 1),
    ...parseAgreement(raw, where),
  };
}

export function parseScenario(value: unknown, index: number): SarEvalScenario {
  const where = `scenarios[${index}]`;
  const raw = asRecord(value, where);
  assertKeys(raw, ["scenarioId", "typology", "variant", "singleWriter", "multiAgent"], where);
  return {
    scenarioId: asString(raw.scenarioId, `${where}.scenarioId`),
    typology: asLiteral(raw.typology, SAR_EVAL_TYPOLOGIES, `${where}.typology`),
    variant: asLiteral(raw.variant, SAR_EVAL_VARIANTS, `${where}.variant`),
    singleWriter: parseScenarioArm(raw.singleWriter, `${where}.singleWriter`),
    multiAgent: parseScenarioArm(raw.multiAgent, `${where}.multiAgent`),
  };
}

export function assertCompleteArms<T extends { arm: SarEvalArm }>(
  values: T[],
  where: string,
): void {
  if (
    values.length !== SAR_EVAL_ARMS.length ||
    SAR_EVAL_ARMS.some((arm) => values.filter((value) => value.arm === arm).length !== 1)
  ) {
    throw new SarEvalStudyError(`${where} must contain each evaluation arm exactly once`);
  }
}

export function assertCompleteDeltas(deltas: SarEvalDelta[]): void {
  if (
    deltas.length !== SAR_EVAL_METRICS.length ||
    SAR_EVAL_METRICS.some(
      (metric) => deltas.filter((delta) => delta.metric === metric).length !== 1,
    )
  ) {
    throw new SarEvalStudyError("summary.deltas must contain each metric exactly once");
  }
}

export function assertScenarioMatrix(scenarios: SarEvalScenario[]): void {
  if (scenarios.length !== EXPECTED_SCENARIO_COUNT) {
    throw new SarEvalStudyError(`scenarios must contain exactly ${EXPECTED_SCENARIO_COUNT} rows`);
  }
  if (new Set(scenarios.map((scenario) => scenario.scenarioId)).size !== scenarios.length) {
    throw new SarEvalStudyError("scenarios must have unique scenarioId values");
  }
  for (const typology of SAR_EVAL_TYPOLOGIES) {
    for (const variant of SAR_EVAL_VARIANTS) {
      const matches = scenarios.filter(
        (scenario) => scenario.typology === typology && scenario.variant === variant,
      );
      if (matches.length !== 1) {
        throw new SarEvalStudyError(`scenarios must contain one ${typology}/${variant} pair`);
      }
    }
  }
}
