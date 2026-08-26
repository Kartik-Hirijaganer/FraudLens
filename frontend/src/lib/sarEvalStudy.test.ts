import { describe, expect, it } from "vitest";

import { sarEvalStudy } from "../test/factories";
import {
  SAR_EVAL_TYPOLOGIES,
  SarEvalStudyError,
  parseSarEvalStudyData,
  sarEvalDelta,
  type SarEvalStudyData,
} from "./sarEvalStudy";

function rawStudy(): Record<string, unknown> {
  return structuredClone(sarEvalStudy()) as unknown as Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>;
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return value as Record<string, unknown>[];
}

function summary(data: Record<string, unknown>): Record<string, unknown> {
  return asRecord(data.summary);
}

function armSummary(data: Record<string, unknown>, index = 0): Record<string, unknown> {
  return asRecords(summary(data).arms)[index];
}

function delta(data: Record<string, unknown>, index = 0): Record<string, unknown> {
  return asRecords(summary(data).deltas)[index];
}

function scenario(data: Record<string, unknown>, index = 0): Record<string, unknown> {
  return asRecords(data.scenarios)[index];
}

describe("parseSarEvalStudyData", () => {
  it("parses the complete fixed protocol", () => {
    const parsed = parseSarEvalStudyData(sarEvalStudy());
    expect(parsed.scenarios).toHaveLength(32);
    expect(new Set(parsed.scenarios.map((scenario) => scenario.typology))).toEqual(
      new Set(SAR_EVAL_TYPOLOGIES),
    );
    expect(parsed.summary.arms.map((arm) => arm.arm)).toEqual(["single_writer", "multi_agent"]);
    expect(parsed.judge).toMatchObject({
      samplesPerNarrative: 3,
      blind: true,
      orderRandomized: true,
    });
    expect(sarEvalDelta(parsed, "completenessRate").pointEstimate).toBe(0.1);
  });

  it.each([
    ["a non-object root", () => null, /must be an object/],
    ["an unexpected root field", () => ({ ...rawStudy(), extra: true }), /keys must be exactly/],
    [
      "an invalid report hash",
      () => ({ ...rawStudy(), reportSha256: "abc" }),
      /64-character lowercase hex/,
    ],
    ["an empty run id", () => ({ ...rawStudy(), runId: "" }), /non-empty string/],
    ["a non-integer seed", () => ({ ...rawStudy(), seed: 1.5 }), /integer >= 0/],
    ["a negative seed", () => ({ ...rawStudy(), seed: -1 }), /integer >= 0/],
    ["a non-synthetic artifact", () => ({ ...rawStudy(), syntheticData: false }), /must be true/],
    ["the wrong scenario count", () => ({ ...rawStudy(), scenarioCount: 31 }), /must be 32/],
    [
      "the wrong bootstrap count",
      () => ({ ...rawStudy(), bootstrapResamples: 999 }),
      /must be 10000/,
    ],
  ])("rejects %s", (_label, build, matcher) => {
    expect(() => parseSarEvalStudyData(build())).toThrow(matcher);
  });

  it("requires the frozen blind, randomized, three-sample judge protocol", () => {
    for (const [field, value, matcher] of [
      ["samplesPerNarrative", 2, /must be 3/],
      ["blind", false, /blind and order-randomized/],
      ["orderRandomized", false, /blind and order-randomized/],
    ] as const) {
      const data = rawStudy();
      asRecord(data.judge)[field] = value;
      expect(() => parseSarEvalStudyData(data)).toThrow(matcher);
    }
  });

  it("validates judge strings, prompt hash, and exact keys", () => {
    const emptyModel = rawStudy();
    asRecord(emptyModel.judge).modelId = " ";
    expect(() => parseSarEvalStudyData(emptyModel)).toThrow(/non-empty string/);

    const badHash = rawStudy();
    asRecord(badHash.judge).promptHash = "A".repeat(64);
    expect(() => parseSarEvalStudyData(badHash)).toThrow(/lowercase hex/);

    const extra = rawStudy();
    asRecord(extra.judge).temperature = 0;
    expect(() => parseSarEvalStudyData(extra)).toThrow(/judge keys must be exactly/);
  });

  it("requires exactly one provenance record for each arm", () => {
    const notArray = rawStudy();
    notArray.armProvenance = {};
    expect(() => parseSarEvalStudyData(notArray)).toThrow(/must be an array/);

    const duplicate = rawStudy();
    asRecords(duplicate.armProvenance)[1].arm = "single_writer";
    expect(() => parseSarEvalStudyData(duplicate)).toThrow(/each evaluation arm exactly once/);

    const unknown = rawStudy();
    asRecords(unknown.armProvenance)[0].arm = "ensemble";
    expect(() => parseSarEvalStudyData(unknown)).toThrow(/must be one of/);
  });

  it("validates provenance arrays, hashes, graph versions, and exact keys", () => {
    const emptyModels = rawStudy();
    asRecords(emptyModels.armProvenance)[0].modelIds = [];
    expect(() => parseSarEvalStudyData(emptyModels)).toThrow(/non-empty array/);

    const duplicateVersions = rawStudy();
    asRecords(duplicateVersions.armProvenance)[0].promptVersions = ["v1", "v1"];
    expect(() => parseSarEvalStudyData(duplicateVersions)).toThrow(/must not contain duplicates/);

    const badHash = rawStudy();
    asRecords(badHash.armProvenance)[0].promptHashes = ["bad"];
    expect(() => parseSarEvalStudyData(badHash)).toThrow(/64-character lowercase hex/);

    const emptyGraph = rawStudy();
    asRecords(emptyGraph.armProvenance)[1].graphVersion = "";
    expect(() => parseSarEvalStudyData(emptyGraph)).toThrow(/non-empty string/);

    const extra = rawStudy();
    asRecords(extra.armProvenance)[0].extra = "nope";
    expect(() => parseSarEvalStudyData(extra)).toThrow(/keys must be exactly/);
  });

  it("proves each writer model is in its arm and from a different family than the judge", () => {
    const absentWriter = rawStudy();
    asRecords(absentWriter.armProvenance)[0].writerModelId = "openrouter/openai/other-model";
    expect(() => parseSarEvalStudyData(absentWriter)).toThrow(/must be present in modelIds/);

    const sameFamily = rawStudy();
    asRecords(sameFamily.armProvenance)[1].writerModelId = "openrouter/anthropic/claude-sonnet-4.6";
    asRecords(sameFamily.armProvenance)[1].writerModelFamily = "anthropic";
    expect(() => parseSarEvalStudyData(sameFamily)).toThrow(/must differ from judge.modelFamily/);

    const emptyFamily = rawStudy();
    asRecords(emptyFamily.armProvenance)[0].writerModelFamily = "";
    expect(() => parseSarEvalStudyData(emptyFamily)).toThrow(/non-empty string/);
  });

  it("derives writer family from provider/model references without substring matching", () => {
    const ambiguous = rawStudy();
    const provenance = asRecords(ambiguous.armProvenance)[0];
    provenance.writerModelId = "openrouter/not-openai/openai-looking-model";
    provenance.modelIds = [provenance.writerModelId];
    expect(() => parseSarEvalStudyData(ambiguous)).toThrow(/must match writerModelId family/);

    const malformed = rawStudy();
    const malformedProvenance = asRecords(malformed.armProvenance)[0];
    malformedProvenance.writerModelId = "gpt-5-mini";
    malformedProvenance.modelIds = [malformedProvenance.writerModelId];
    expect(() => parseSarEvalStudyData(malformed)).toThrow(/router\/family\/model reference/);

    const incompleteRoute = rawStudy();
    const incompleteProvenance = asRecords(incompleteRoute.armProvenance)[0];
    incompleteProvenance.writerModelId = "openrouter/openai";
    incompleteProvenance.modelIds = [incompleteProvenance.writerModelId];
    expect(() => parseSarEvalStudyData(incompleteRoute)).toThrow(/router\/family\/model reference/);

    const alternateRouter = rawStudy();
    const alternateProvenance = asRecords(alternateRouter.armProvenance)[0];
    alternateProvenance.writerModelId = "gateway/openai/gpt-5-mini";
    alternateProvenance.modelIds = [alternateProvenance.writerModelId];
    expect(parseSarEvalStudyData(alternateRouter).armProvenance[0].writerModelFamily).toBe(
      "openai",
    );
  });

  it("derives and validates the judge family from the canonical model reference", () => {
    const mismatch = rawStudy();
    asRecord(mismatch.judge).modelFamily = "openai";
    expect(() => parseSarEvalStudyData(mismatch)).toThrow(/judge.modelFamily must match/);

    const shortRef = rawStudy();
    asRecord(shortRef.judge).modelId = "anthropic/claude-opus-4.6";
    expect(() => parseSarEvalStudyData(shortRef)).toThrow(/router\/family\/model reference/);
  });

  it("requires summary arms and deltas arrays", () => {
    const badSummary = rawStudy();
    badSummary.summary = [];
    expect(() => parseSarEvalStudyData(badSummary)).toThrow(/summary must be an object/);

    const badArms = rawStudy();
    summary(badArms).arms = {};
    expect(() => parseSarEvalStudyData(badArms)).toThrow(/must be arrays/);

    const duplicateArm = rawStudy();
    armSummary(duplicateArm, 1).arm = "single_writer";
    expect(() => parseSarEvalStudyData(duplicateArm)).toThrow(/each evaluation arm exactly once/);
  });

  it("validates every arm summary measure", () => {
    for (const [field, value, matcher] of [
      ["completenessRate", 1.1, /must be in \[0, 1\]/],
      ["unsupportedClaims", -1, /must be non-negative/],
      ["citationPrecision", Number.NaN, /must be a finite number/],
      ["citationRecall", -0.1, /must be in \[0, 1\]/],
      ["fabricatedCitationCount", -1, /must be non-negative/],
      ["costUsd", -0.01, /must be non-negative/],
      ["latencyMs", -1, /must be non-negative/],
      ["modelCalls", 0, /must be positive/],
      ["elementAgreement", 2, /must be in \[0, 1\]/],
      ["unsupportedClaimCountAgreement", -1, /must be in \[0, 1\]/],
      ["unsupportedClaimSpanAgreement", 2, /must be in \[0, 1\]/],
      ["agreement", 2, /must be in \[0, 1\]/],
    ] as const) {
      const data = rawStudy();
      armSummary(data)[field] = value;
      expect(() => parseSarEvalStudyData(data)).toThrow(matcher);
    }

    const fractionalCalls = rawStudy();
    armSummary(fractionalCalls).modelCalls = 1.5;
    expect(parseSarEvalStudyData(fractionalCalls).summary.arms[0].modelCalls).toBe(1.5);
  });

  it("requires summary and scenario composite agreement to equal the three-component mean", () => {
    const summaryMismatch = rawStudy();
    armSummary(summaryMismatch).elementAgreement = 0.5;
    expect(() => parseSarEvalStudyData(summaryMismatch)).toThrow(/equal the mean/);

    const scenarioMismatch = rawStudy();
    asRecord(scenario(scenarioMismatch).multiAgent).unsupportedClaimSpanAgreement = 0.6;
    expect(() => parseSarEvalStudyData(scenarioMismatch)).toThrow(/equal the mean/);
  });

  it("requires each paired delta exactly once", () => {
    const missing = rawStudy();
    asRecords(summary(missing).deltas).pop();
    expect(() => parseSarEvalStudyData(missing)).toThrow(/each metric exactly once/);

    const duplicate = rawStudy();
    delta(duplicate, 1).metric = "completenessRate";
    expect(() => parseSarEvalStudyData(duplicate)).toThrow(/each metric exactly once/);

    const unknown = rawStudy();
    delta(unknown).metric = "accuracy";
    expect(() => parseSarEvalStudyData(unknown)).toThrow(/must be one of/);
  });

  it("derives significance from an ordered finite interval", () => {
    const inverted = rawStudy();
    delta(inverted).ciLower = 0.2;
    delta(inverted).ciUpper = 0.1;
    expect(() => parseSarEvalStudyData(inverted)).toThrow(/lower bound exceeds/);

    const falseClaim = rawStudy();
    delta(falseClaim).significant = false;
    expect(() => parseSarEvalStudyData(falseClaim)).toThrow(/interval exclusion of zero/);

    const nonFinite = rawStudy();
    delta(nonFinite).pointEstimate = Number.POSITIVE_INFINITY;
    expect(() => parseSarEvalStudyData(nonFinite)).toThrow(/finite number/);
  });

  it("requires a 32-row, unique 8-by-4 scenario matrix", () => {
    const notArray = rawStudy();
    notArray.scenarios = {};
    expect(() => parseSarEvalStudyData(notArray)).toThrow(/must be an array/);

    const short = rawStudy();
    asRecords(short.scenarios).pop();
    expect(() => parseSarEvalStudyData(short)).toThrow(/exactly 32 rows/);

    const duplicateId = rawStudy();
    scenario(duplicateId, 1).scenarioId = scenario(duplicateId).scenarioId;
    expect(() => parseSarEvalStudyData(duplicateId)).toThrow(/unique scenarioId/);

    const unknownTypology = rawStudy();
    scenario(unknownTypology).typology = "smurfing";
    expect(() => parseSarEvalStudyData(unknownTypology)).toThrow(/must be one of/);

    const duplicatePair = rawStudy();
    scenario(duplicatePair, 1).variant = "clean";
    expect(() => parseSarEvalStudyData(duplicatePair)).toThrow(/must contain one/);
  });

  it("validates scenario identity and both arm cells", () => {
    const emptyId = rawStudy();
    scenario(emptyId).scenarioId = "";
    expect(() => parseSarEvalStudyData(emptyId)).toThrow(/non-empty string/);

    const badVariant = rawStudy();
    scenario(badVariant).variant = "noisy";
    expect(() => parseSarEvalStudyData(badVariant)).toThrow(/must be one of/);

    const nonObjectArm = rawStudy();
    scenario(nonObjectArm).singleWriter = [];
    expect(() => parseSarEvalStudyData(nonObjectArm)).toThrow(/must be an object/);

    const extra = rawStudy();
    asRecord(scenario(extra).multiAgent).extra = true;
    expect(() => parseSarEvalStudyData(extra)).toThrow(/keys must be exactly/);
  });

  it("validates scenario completeness, counts, rates, and telemetry", () => {
    for (const [field, value, matcher] of [
      ["completenessPassed", 6, /must be in \[0, 5\]/],
      ["completenessPassed", 1.5, /integer >= 0/],
      ["unsupportedClaimCount", -1, /integer >= 0/],
      ["citationPrecision", 1.1, /must be in \[0, 1\]/],
      ["citationRecall", -0.1, /must be in \[0, 1\]/],
      ["fabricatedCitationCount", 0.5, /integer >= 0/],
      ["costUsd", -1, /must be non-negative/],
      ["latencyMs", -1, /must be non-negative/],
      ["modelCalls", 0, /integer >= 1/],
      ["modelCalls", 1.5, /integer >= 1/],
      ["elementAgreement", 1.1, /must be in \[0, 1\]/],
      ["unsupportedClaimCountAgreement", -0.1, /must be in \[0, 1\]/],
      ["unsupportedClaimSpanAgreement", 1.1, /must be in \[0, 1\]/],
      ["agreement", 2, /must be in \[0, 1\]/],
    ] as const) {
      const data = rawStudy();
      asRecord(scenario(data).multiAgent)[field] = value;
      expect(() => parseSarEvalStudyData(data)).toThrow(matcher);
    }
  });
});

describe("sarEvalDelta", () => {
  it("fails closed if a supposedly validated object loses a required delta", () => {
    const parsed = parseSarEvalStudyData(sarEvalStudy());
    const drifted = {
      ...parsed,
      summary: { ...parsed.summary, deltas: [] },
    } as SarEvalStudyData;
    expect(() => sarEvalDelta(drifted, "completenessRate")).toThrow(SarEvalStudyError);
  });
});
