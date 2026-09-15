/**
 * Summary: Column definitions and compact paired cells for SAR evaluation study tables.
 *
 * Key classes:
 * - AgreementRow: one inter-sample agreement comparison.
 *
 * Key functions:
 * - METRIC_COLUMNS:
 * - SCENARIO_COLUMNS:
 * - AGREEMENT_COLUMNS:
 *
 * Notes:
 * - Columns consume only validated study contracts.
 */
import { Badge } from "../components/ui/Badge";
import type { Column } from "../components/ui/DataTable";
import { formatCurrency, formatDurationMs, formatPercent } from "../lib/format";
import type { SarEvalScenario } from "../lib/sarEvalStudy";
import {
  TYPOLOGY_LABELS,
  VARIANT_LABELS,
  interval,
  metricDelta,
  metricValue,
  type MetricRow,
} from "./sarEvalStudyMetrics";

export const METRIC_COLUMNS: Column<MetricRow>[] = [
  {
    id: "metric",
    header: "Metric",
    cell: (row) => (
      <div className="gap-xxs flex flex-col">
        <span className="text-ink font-semibold">{row.label}</span>
        <span className="text-caption text-mute">{row.better} is better</span>
      </div>
    ),
  },
  {
    id: "single-writer",
    header: "Single-writer",
    cell: (row) => metricValue(row.metric, row.singleWriter),
    align: "right",
  },
  {
    id: "multi-agent",
    header: "Multi-agent",
    cell: (row) => metricValue(row.metric, row.multiAgent),
    align: "right",
  },
  {
    id: "delta",
    header: "Paired delta",
    cell: (row) => metricDelta(row.metric, row.delta.pointEstimate),
    align: "right",
  },
  {
    id: "interval",
    header: "BCa 95% CI",
    cell: (row) => interval(row.delta),
    align: "right",
  },
  {
    id: "significance",
    header: "Interval result",
    cell: (row) => (
      <Badge tone="neutral">{row.delta.significant ? "Excludes zero" : "Includes zero"}</Badge>
    ),
    align: "right",
  },
];

interface PairedValuesProps {
  label: string;
  singleWriter: string;
  multiAgent: string;
}

function PairedValues({ label, singleWriter, multiAgent }: PairedValuesProps) {
  return (
    <div role="group" aria-label={label} className="gap-xxs flex flex-col whitespace-nowrap">
      <p>
        <span className="text-caption text-mute">Single</span> {singleWriter}
      </p>
      <p>
        <span className="text-caption text-mute">Multi</span> {multiAgent}
      </p>
    </div>
  );
}

export const SCENARIO_COLUMNS: Column<SarEvalScenario>[] = [
  {
    id: "scenario",
    header: "Scenario",
    cell: (row) => <span className="text-ink font-semibold">{row.scenarioId}</span>,
  },
  {
    id: "typology",
    header: "Typology",
    cell: (row) => TYPOLOGY_LABELS[row.typology],
  },
  {
    id: "variant",
    header: "Variant",
    cell: (row) => VARIANT_LABELS[row.variant],
  },
  {
    id: "quality",
    header: "Completeness / unsupported",
    cell: (row) => (
      <PairedValues
        label={`${row.scenarioId} completeness and unsupported claims`}
        singleWriter={`${row.singleWriter.completenessPassed}/5 · ${row.singleWriter.unsupportedClaimCount} unsupported`}
        multiAgent={`${row.multiAgent.completenessPassed}/5 · ${row.multiAgent.unsupportedClaimCount} unsupported`}
      />
    ),
  },
  {
    id: "citations",
    header: "Citation P / R / fabricated",
    cell: (row) => (
      <PairedValues
        label={`${row.scenarioId} citation metrics`}
        singleWriter={`${formatPercent(row.singleWriter.citationPrecision)} / ${formatPercent(row.singleWriter.citationRecall)} / ${row.singleWriter.fabricatedCitationCount}`}
        multiAgent={`${formatPercent(row.multiAgent.citationPrecision)} / ${formatPercent(row.multiAgent.citationRecall)} / ${row.multiAgent.fabricatedCitationCount}`}
      />
    ),
  },
  {
    id: "cost-latency",
    header: "Cost / persisted run duration",
    cell: (row) => (
      <PairedValues
        label={`${row.scenarioId} cost and persisted run duration`}
        singleWriter={`${formatCurrency(row.singleWriter.costUsd, "USD")} · ${formatDurationMs(row.singleWriter.latencyMs)}`}
        multiAgent={`${formatCurrency(row.multiAgent.costUsd, "USD")} · ${formatDurationMs(row.multiAgent.latencyMs)}`}
      />
    ),
  },
  {
    id: "model-calls",
    header: "Model calls",
    cell: (row) => (
      <PairedValues
        label={`${row.scenarioId} model calls`}
        singleWriter={String(row.singleWriter.modelCalls)}
        multiAgent={String(row.multiAgent.modelCalls)}
      />
    ),
    align: "right",
  },
  {
    id: "agreement",
    header: "Agreement",
    cell: (row) => (
      <PairedValues
        label={`${row.scenarioId} judge agreement`}
        singleWriter={formatPercent(row.singleWriter.agreement)}
        multiAgent={formatPercent(row.multiAgent.agreement)}
      />
    ),
    align: "right",
  },
];

export interface AgreementRow {
  id: string;
  label: string;
  singleWriter: number;
  multiAgent: number;
}

export const AGREEMENT_COLUMNS: Column<AgreementRow>[] = [
  {
    id: "measure",
    header: "Agreement measure",
    cell: (row) => <span className="text-ink font-semibold">{row.label}</span>,
  },
  {
    id: "single-writer",
    header: "Single-writer",
    cell: (row) => formatPercent(row.singleWriter),
    align: "right",
  },
  {
    id: "multi-agent",
    header: "Multi-agent",
    cell: (row) => formatPercent(row.multiAgent),
    align: "right",
  },
];
