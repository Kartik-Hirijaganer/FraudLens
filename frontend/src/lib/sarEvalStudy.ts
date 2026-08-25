/**
 * Summary: Typed, fail-closed parser for the committed multi-agent SAR evaluation projection.
 * It validates the fixed 32-scenario protocol, both arm summaries, paired BCa intervals,
 * agreement, and provenance before the lazily loaded research page can render the artifact.
 *
 * Key classes:
 * - SarEvalJudge: published cross-family judge identity and frozen sampling protocol.
 * - SarEvalArmProvenance: model, prompt, and graph provenance for one compared workflow.
 * - SarEvalAgreement: component inter-sample agreement rates and their validated mean.
 * - SarEvalArmSummary: aggregate quality, citation, cost, latency, call, and agreement measures.
 * - SarEvalDelta: one paired multi-agent-minus-single-writer estimate and BCa interval.
 * - SarEvalScenarioArm: aggregate measurements for one scenario/workflow cell.
 * - SarEvalScenario: one paired typology/variant record in the fixed scenario matrix.
 * - SarEvalStudyData: the complete browser-safe projection of the offline evaluation report.
 * - SarEvalStudyError: the parse failure raised when a committed artifact drifts from protocol.
 *
 * Key functions:
 * - SAR_EVAL_ARMS: the two workflow arms required in summaries and provenance.
 * - SAR_EVAL_TYPOLOGIES: the eight canonical synthetic SAR scenario typologies.
 * - SAR_EVAL_VARIANTS: the four adversarial variants required for every typology.
 * - SAR_EVAL_METRICS: the judge-scored and programmatic paired metrics required in publication.
 * - parseSarEvalStudyData: validate unknown JSON into the strict frontend study contract.
 * - sarEvalDelta: find one required paired metric delta in a validated study.
 *
 * Notes:
 * - The projection contains aggregate synthetic results only; raw narratives and judge spans stay
 *   in the bound documentation report. This module performs no network or runtime API access.
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

const HASH_PATTERN = /^[0-9a-f]{64}$/;
const EXPECTED_SCENARIO_COUNT = 32;
const EXPECTED_BOOTSTRAP_RESAMPLES = 10_000;
const EXPECTED_JUDGE_SAMPLES = 3;
const FINCEN_ELEMENT_COUNT = 5;
const AGREEMENT_MEAN_TOLERANCE = 1e-9;

function asRecord(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new SarEvalStudyError(`${where} must be an object`);
  }
  return value as Record<string, unknown>;
}

function assertKeys(
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

function asString(value: unknown, where: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new SarEvalStudyError(`${where} must be a non-empty string`);
  }
  return value;
}

function asHash(value: unknown, where: string): string {
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

function asInteger(value: unknown, where: string, minimum = 0): number {
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

function familyFromModelRef(modelId: string, where: string): string {
  const segments = modelId.split("/");
  if (segments.length < 3 || segments.some((segment) => segment.length === 0)) {
    throw new SarEvalStudyError(`${where} must be a router/family/model reference`);
  }
  return segments[1];
}

function parseJudge(value: unknown): SarEvalJudge {
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

function parseArmProvenance(value: unknown, index: number): SarEvalArmProvenance {
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

function parseArmSummary(value: unknown, index: number): SarEvalArmSummary {
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

function parseDelta(value: unknown, index: number): SarEvalDelta {
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

function parseScenario(value: unknown, index: number): SarEvalScenario {
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

function assertCompleteArms<T extends { arm: SarEvalArm }>(values: T[], where: string): void {
  if (
    values.length !== SAR_EVAL_ARMS.length ||
    SAR_EVAL_ARMS.some((arm) => values.filter((value) => value.arm === arm).length !== 1)
  ) {
    throw new SarEvalStudyError(`${where} must contain each evaluation arm exactly once`);
  }
}

function assertCompleteDeltas(deltas: SarEvalDelta[]): void {
  if (
    deltas.length !== SAR_EVAL_METRICS.length ||
    SAR_EVAL_METRICS.some(
      (metric) => deltas.filter((delta) => delta.metric === metric).length !== 1,
    )
  ) {
    throw new SarEvalStudyError("summary.deltas must contain each metric exactly once");
  }
}

function assertScenarioMatrix(scenarios: SarEvalScenario[]): void {
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

export function parseSarEvalStudyData(value: unknown): SarEvalStudyData {
  const raw = asRecord(value, "study data");
  assertKeys(
    raw,
    [
      "reportSha256",
      "runId",
      "seed",
      "syntheticData",
      "scenarioCount",
      "bootstrapResamples",
      "judge",
      "armProvenance",
      "summary",
      "scenarios",
    ],
    "study data",
  );
  if (raw.syntheticData !== true) {
    throw new SarEvalStudyError("syntheticData must be true");
  }
  if (raw.scenarioCount !== EXPECTED_SCENARIO_COUNT) {
    throw new SarEvalStudyError(`scenarioCount must be ${EXPECTED_SCENARIO_COUNT}`);
  }
  if (raw.bootstrapResamples !== EXPECTED_BOOTSTRAP_RESAMPLES) {
    throw new SarEvalStudyError(`bootstrapResamples must be ${EXPECTED_BOOTSTRAP_RESAMPLES}`);
  }
  if (!Array.isArray(raw.armProvenance)) {
    throw new SarEvalStudyError("armProvenance must be an array");
  }
  const judge = parseJudge(raw.judge);
  const parsedJudgeFamily = familyFromModelRef(judge.modelId, "judge.modelId");
  if (parsedJudgeFamily !== judge.modelFamily) {
    throw new SarEvalStudyError(
      `judge.modelFamily must match judge.modelId family ${parsedJudgeFamily}`,
    );
  }
  const armProvenance = raw.armProvenance.map(parseArmProvenance);
  assertCompleteArms(armProvenance, "armProvenance");
  for (const provenance of armProvenance) {
    if (!provenance.modelIds.includes(provenance.writerModelId)) {
      throw new SarEvalStudyError(
        `armProvenance.${provenance.arm}.writerModelId must be present in modelIds`,
      );
    }
    const parsedWriterFamily = familyFromModelRef(
      provenance.writerModelId,
      `armProvenance.${provenance.arm}.writerModelId`,
    );
    if (parsedWriterFamily !== provenance.writerModelFamily) {
      throw new SarEvalStudyError(
        `armProvenance.${provenance.arm}.writerModelFamily must match writerModelId family ${parsedWriterFamily}`,
      );
    }
    if (provenance.writerModelFamily === judge.modelFamily) {
      throw new SarEvalStudyError(
        `armProvenance.${provenance.arm}.writerModelFamily must differ from judge.modelFamily`,
      );
    }
  }

  const summaryRaw = asRecord(raw.summary, "summary");
  assertKeys(summaryRaw, ["arms", "deltas"], "summary");
  if (!Array.isArray(summaryRaw.arms) || !Array.isArray(summaryRaw.deltas)) {
    throw new SarEvalStudyError("summary.arms and summary.deltas must be arrays");
  }
  const arms = summaryRaw.arms.map(parseArmSummary);
  const deltas = summaryRaw.deltas.map(parseDelta);
  assertCompleteArms(arms, "summary.arms");
  assertCompleteDeltas(deltas);

  if (!Array.isArray(raw.scenarios)) {
    throw new SarEvalStudyError("scenarios must be an array");
  }
  const scenarios = raw.scenarios.map(parseScenario);
  assertScenarioMatrix(scenarios);

  return {
    reportSha256: asHash(raw.reportSha256, "reportSha256"),
    runId: asString(raw.runId, "runId"),
    seed: asInteger(raw.seed, "seed"),
    syntheticData: true,
    scenarioCount: EXPECTED_SCENARIO_COUNT,
    bootstrapResamples: EXPECTED_BOOTSTRAP_RESAMPLES,
    judge,
    armProvenance,
    summary: { arms, deltas },
    scenarios,
  };
}

export function sarEvalDelta(data: SarEvalStudyData, metric: SarEvalMetric): SarEvalDelta {
  const delta = data.summary.deltas.find((candidate) => candidate.metric === metric);
  if (!delta) {
    throw new SarEvalStudyError(`validated study is missing ${metric}`);
  }
  return delta;
}
