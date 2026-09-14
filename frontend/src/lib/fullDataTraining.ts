/**
 * Summary: Strict parser and browser contract for the published IBM full-data aggregate study.
 *
 * Key classes:
 * - FullDataCandidate: one pre-registered candidate's reconciliation and holdout metrics.
 * - FullDataTrainingData: hash-bound browser projection of the measured training report.
 *
 * Key functions:
 * - parseFullDataTrainingData: reject malformed, incomplete, or internally inconsistent JSON.
 *
 * Notes:
 * - The projection contains counts and metrics only; it never contains transaction rows.
 */
import {
  arrayOf,
  booleanOf,
  exactKeys,
  integerOf,
  numberOf,
  recordOf,
  stringOf,
  ArtifactError,
} from "./artifactGuards";

export interface FullDataCandidate {
  candidate: string;
  source: string;
  sourceRows: number;
  usableRows: number;
  trainingRows: number;
  evaluationRows: number;
  prAuc: number;
  baselinePrAuc: number;
  recallAtBudget: number;
  gatesPassed: boolean;
}

export interface FullDataTrainingData {
  reportSha256: string;
  runId: string;
  provenance: string;
  applicationCandidate: string;
  candidates: FullDataCandidate[];
}

const CANDIDATE_KEYS = [
  "candidate",
  "source",
  "sourceRows",
  "usableRows",
  "trainingRows",
  "evaluationRows",
  "prAuc",
  "baselinePrAuc",
  "recallAtBudget",
  "gatesPassed",
];

function rateOf(value: unknown, path: string): number {
  const parsed = numberOf(value, path);
  if (parsed < 0 || parsed > 1) throw new ArtifactError(`${path} must be within [0, 1]`);
  return parsed;
}

function candidateOf(value: unknown, index: number): FullDataCandidate {
  const path = `candidates[${index}]`;
  const raw = recordOf(value, path);
  exactKeys(raw, CANDIDATE_KEYS, path);
  const candidate = {
    candidate: stringOf(raw.candidate, `${path}.candidate`),
    source: stringOf(raw.source, `${path}.source`),
    sourceRows: integerOf(raw.sourceRows, `${path}.sourceRows`),
    usableRows: integerOf(raw.usableRows, `${path}.usableRows`),
    trainingRows: integerOf(raw.trainingRows, `${path}.trainingRows`),
    evaluationRows: integerOf(raw.evaluationRows, `${path}.evaluationRows`),
    prAuc: rateOf(raw.prAuc, `${path}.prAuc`),
    baselinePrAuc: rateOf(raw.baselinePrAuc, `${path}.baselinePrAuc`),
    recallAtBudget: rateOf(raw.recallAtBudget, `${path}.recallAtBudget`),
    gatesPassed: booleanOf(raw.gatesPassed, `${path}.gatesPassed`),
  };
  if (candidate.usableRows > candidate.sourceRows) {
    throw new ArtifactError(`${path}.usableRows cannot exceed sourceRows`);
  }
  return candidate;
}

export function parseFullDataTrainingData(value: unknown): FullDataTrainingData {
  const raw = recordOf(value, "full-data artifact");
  exactKeys(
    raw,
    ["reportSha256", "runId", "provenance", "applicationCandidate", "candidates"],
    "full-data artifact",
  );
  const reportSha256 = stringOf(raw.reportSha256, "reportSha256");
  if (!/^[0-9a-f]{64}$/.test(reportSha256)) throw new ArtifactError("invalid reportSha256");
  const candidates = arrayOf(raw.candidates, "candidates").map(candidateOf);
  if (candidates.length === 0) throw new ArtifactError("candidates must not be empty");
  const applicationCandidate = stringOf(raw.applicationCandidate, "applicationCandidate");
  if (!candidates.some((item) => item.candidate === applicationCandidate)) {
    throw new ArtifactError("applicationCandidate must identify a published candidate");
  }
  return {
    reportSha256,
    runId: stringOf(raw.runId, "runId"),
    provenance: stringOf(raw.provenance, "provenance"),
    applicationCandidate,
    candidates,
  };
}
