/**
 * Summary: Stable barrel and fail-closed parser for the committed SAR evaluation projection.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - parseSarEvalStudyData: validate unknown JSON into the strict study contract.
 * - sarEvalDelta: select one required paired metric delta.
 *
 * Notes:
 * - Existing imports remain valid through explicit barrel exports.
 */
import {
  SarEvalStudyError,
  type SarEvalDelta,
  type SarEvalMetric,
  type SarEvalStudyData,
} from "./sarEvalStudyTypes";
import {
  EXPECTED_BOOTSTRAP_RESAMPLES,
  EXPECTED_SCENARIO_COUNT,
  asHash,
  asInteger,
  asRecord,
  asString,
  assertCompleteArms,
  assertCompleteDeltas,
  assertKeys,
  assertScenarioMatrix,
  familyFromModelRef,
  parseArmProvenance,
  parseArmSummary,
  parseDelta,
  parseJudge,
  parseScenario,
} from "./sarEvalStudyGuards";

export * from "./sarEvalStudyTypes";

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
