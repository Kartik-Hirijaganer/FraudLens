/**
 * Summary: Formatting, metric definitions, rows, and sign-honest SAR study headline logic.
 *
 * Key classes:
 * - MetricRow: one paired aggregate metric row.
 *
 * Key functions:
 * - QUALITY_METRICS:
 * - PROGRAMMATIC_METRICS:
 * - VARIANT_LABELS:
 * - TYPOLOGY_LABELS:
 * - armLabel:
 * - metricValue: format an arm value by metric semantics.
 * - metricDelta: format a signed paired difference.
 * - interval:
 * - summaryFor:
 * - rowsFor:
 * - sarEvalHeadline: derive an honest quality conclusion from measured signs.
 *
 * Notes:
 * - The artifact cannot provide authored favorable headline prose.
 */
import { formatCurrency, formatDurationMs, formatPercent } from "../lib/format";
import {
  SAR_EVAL_VARIANTS,
  sarEvalDelta,
  type SarEvalArm,
  type SarEvalArmSummary,
  type SarEvalDelta,
  type SarEvalMetric,
  type SarEvalStudyData,
  type SarEvalTypology,
} from "../lib/sarEvalStudy";

interface MetricDefinition {
  metric: SarEvalMetric;
  label: string;
  better: "higher" | "lower";
}

export interface MetricRow extends MetricDefinition {
  singleWriter: number;
  multiAgent: number;
  delta: SarEvalDelta;
}

export const QUALITY_METRICS: readonly MetricDefinition[] = [
  { metric: "completenessRate", label: "FinCEN narrative completeness", better: "higher" },
  { metric: "unsupportedClaims", label: "Unsupported claims", better: "lower" },
];

export const PROGRAMMATIC_METRICS: readonly MetricDefinition[] = [
  { metric: "citationPrecision", label: "Citation precision", better: "higher" },
  { metric: "citationRecall", label: "Citation recall", better: "higher" },
  { metric: "fabricatedCitationCount", label: "Fabricated citations", better: "lower" },
  { metric: "costUsd", label: "Cost per narrative", better: "lower" },
  { metric: "latencyMs", label: "Latency per narrative", better: "lower" },
  { metric: "modelCalls", label: "Model calls per narrative", better: "lower" },
];

export const VARIANT_LABELS: Record<(typeof SAR_EVAL_VARIANTS)[number], string> = {
  clean: "Clean",
  thin_evidence: "Thin evidence",
  conflicting_evidence: "Conflicting evidence",
  citation_bait: "Citation bait",
};

export const TYPOLOGY_LABELS: Record<SarEvalTypology, string> = {
  structuring: "Structuring",
  high_risk_wire: "High-risk wire",
  rapid_movement: "Rapid movement",
  funnel_account: "Funnel account",
  mule_velocity: "Mule velocity",
  round_amount_layering: "Round-amount layering",
  crypto_off_ramp: "Crypto off-ramp",
  shell_company_transfer: "Shell-company transfer",
};

export function armLabel(arm: SarEvalArm): string {
  return arm === "single_writer" ? "Single-writer" : "Multi-agent";
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function metricValue(metric: SarEvalMetric, value: number): string {
  if (
    metric === "completenessRate" ||
    metric === "citationPrecision" ||
    metric === "citationRecall"
  ) {
    return formatPercent(value);
  }
  if (metric === "costUsd") {
    return formatCurrency(value, "USD");
  }
  if (metric === "latencyMs") {
    return formatDurationMs(value);
  }
  return value.toFixed(2);
}

export function metricDelta(metric: SarEvalMetric, value: number): string {
  if (
    metric === "completenessRate" ||
    metric === "citationPrecision" ||
    metric === "citationRecall"
  ) {
    return `${signed(value * 100, 1)} pp`;
  }
  if (metric === "costUsd") {
    return `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(4)}`;
  }
  if (metric === "latencyMs") {
    return `${value >= 0 ? "+" : "−"}${formatDurationMs(Math.abs(value))}`;
  }
  return signed(value);
}

export function interval(delta: SarEvalDelta): string {
  return `[${metricDelta(delta.metric, delta.ciLower)}, ${metricDelta(delta.metric, delta.ciUpper)}]`;
}

export function summaryFor(data: SarEvalStudyData, arm: SarEvalArm): SarEvalArmSummary {
  const summary = data.summary.arms.find((candidate) => candidate.arm === arm);
  if (!summary) {
    throw new Error(`validated SAR evaluation is missing ${arm}`);
  }
  return summary;
}

export function rowsFor(
  data: SarEvalStudyData,
  singleWriter: SarEvalArmSummary,
  multiAgent: SarEvalArmSummary,
  definitions: readonly MetricDefinition[],
): MetricRow[] {
  return definitions.map((definition) => ({
    ...definition,
    singleWriter: singleWriter[definition.metric],
    multiAgent: multiAgent[definition.metric],
    delta: sarEvalDelta(data, definition.metric),
  }));
}

function direction(delta: SarEvalDelta, better: MetricDefinition["better"]): -1 | 0 | 1 {
  if (delta.pointEstimate === 0) {
    return 0;
  }
  const sign = delta.pointEstimate > 0 ? 1 : -1;
  return better === "higher" ? sign : sign === 1 ? -1 : 1;
}

export function sarEvalHeadline(data: SarEvalStudyData): string {
  const completeness = sarEvalDelta(data, "completenessRate");
  const unsupported = sarEvalDelta(data, "unsupportedClaims");
  const outcomes = [direction(completeness, "higher"), direction(unsupported, "lower")];
  const completenessPhrase =
    completeness.pointEstimate === 0
      ? "left five-element narrative completeness unchanged"
      : `${completeness.pointEstimate > 0 ? "raised" : "lowered"} five-element narrative completeness by ${Math.abs(completeness.pointEstimate * 100).toFixed(1)} percentage points`;
  const unsupportedPhrase =
    unsupported.pointEstimate === 0
      ? "left unsupported claims unchanged"
      : `${unsupported.pointEstimate < 0 ? "reduced" : "increased"} unsupported claims by ${Math.abs(unsupported.pointEstimate).toFixed(2)} per narrative`;

  if (outcomes.every((outcome) => outcome >= 0) && outcomes.some((outcome) => outcome > 0)) {
    return `Multi-agent drafting improved the paired quality result: it ${completenessPhrase} and ${unsupportedPhrase}.`;
  }
  if (outcomes.every((outcome) => outcome <= 0) && outcomes.some((outcome) => outcome < 0)) {
    return `Multi-agent drafting underperformed the single writer: it ${completenessPhrase} and ${unsupportedPhrase}.`;
  }
  if (outcomes.every((outcome) => outcome === 0)) {
    return "Multi-agent drafting tied the single writer on narrative completeness and unsupported claims.";
  }
  return `Multi-agent drafting produced a mixed quality result: it ${completenessPhrase} and ${unsupportedPhrase}.`;
}
