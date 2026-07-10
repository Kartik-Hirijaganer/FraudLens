/**
 * Summary: Centralized display options and metric definitions shared by pages and
 * components. Keeping these arrays here removes duplicated filter/status/label
 * declarations and keeps display names aligned with the typed API values.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - RISK_BAND_OPTIONS: shared transaction risk filter options.
 * - ALERT_STATUS_OPTIONS: shared alert status filter options.
 * - TRAINING_LABEL_OPTIONS: shared training-label options.
 * - CANARY_RAMP_STEPS: shared canary rollout percentages.
 * - MODEL_METRIC_DEFINITIONS: shared model metric display definitions.
 * - extractModelMetrics: normalize loose registry metrics into displayable values.
 *
 * Notes:
 * - The model registry stores metrics as JSON; this file is the single UI reader for
 *   precision/recall/AUC-style values.
 */
import {
  STATUS_LABELS,
  type AlertStatus,
  type CanaryPercent,
  type ModelVersionResponse,
  type TrainingLabel,
} from "./api";

export const RISK_BAND_OPTIONS = [
  { value: "", label: "All" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "critical", label: "Critical" },
] as const;

export const ALERT_STATUS_OPTIONS: ReadonlyArray<{ value: "" | AlertStatus; label: string }> = [
  { value: "", label: "All" },
  { value: "open", label: STATUS_LABELS.open },
  { value: "in_review", label: STATUS_LABELS.in_review },
  { value: "pending_review", label: STATUS_LABELS.pending_review },
  { value: "resolved", label: STATUS_LABELS.resolved },
  { value: "dismissed", label: STATUS_LABELS.dismissed },
  { value: "escalated", label: STATUS_LABELS.escalated },
];

export const TRAINING_LABEL_OPTIONS: ReadonlyArray<{ value: TrainingLabel; label: string }> = [
  { value: "confirmed_fraud", label: "Confirmed fraud" },
  { value: "false_positive", label: "False positive" },
  { value: "false_negative", label: "False negative" },
  { value: "benign", label: "Benign" },
];

export const CANARY_RAMP_STEPS: CanaryPercent[] = [5, 25, 50, 100];

interface ExtractedModelMetrics {
  precision: number | null;
  recall: number | null;
  auc: number | null;
}

interface MetricDisplayDefinition {
  label: string;
  key: keyof ExtractedModelMetrics;
  format: (value: number | null) => string;
}

function readMetric(metrics: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = metrics[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function formatMetric(value: number | null): string {
  return value === null ? "—" : value.toFixed(3);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export const MODEL_METRIC_DEFINITIONS: ReadonlyArray<MetricDisplayDefinition> = [
  { label: "Precision", key: "precision", format: formatMetric },
  { label: "Recall", key: "recall", format: formatMetric },
  { label: "AUC", key: "auc", format: formatMetric },
];

export function extractModelMetrics(
  metricsOrVersion: Record<string, unknown> | ModelVersionResponse | null | undefined,
): ExtractedModelMetrics {
  const source: unknown = metricsOrVersion ?? {};
  const metricsCandidate = isRecord(source) ? source.metrics : null;
  const metrics: Record<string, unknown> = isRecord(metricsCandidate)
    ? metricsCandidate
    : isRecord(source)
      ? source
      : {};
  return {
    precision: readMetric(metrics, ["precision"]),
    recall: readMetric(metrics, ["recall"]),
    auc: readMetric(metrics, ["auc", "rocAuc", "prAuc"]),
  };
}
