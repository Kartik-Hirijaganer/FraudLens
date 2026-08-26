/**
 * Summary: Authenticated research page for the committed multi-agent SAR drafting evaluation. It
 * presents sign-honest quality findings, programmatic metrics, judge stability, and reproducible
 * protocol/provenance from one validated public-synthetic artifact, with no backend call.
 *
 * Key classes:
 * - SarEvalStudyProps: the validated browser-safe study projection rendered by the page.
 *
 * Key functions:
 * - ADR_019_HREF: expose the canonical ADR Markdown through a browser-readable local URL.
 * - sarEvalHeadline: derive an honest improved, losing, mixed, or tied quality conclusion.
 * - SarEvalStudy: render the evaluation finding, comparisons, agreement, and provenance.
 * - SAR_EVAL_STUDY_PATH: the canonical hash route for this research page.
 *
 * Notes:
 * - Headline wording is derived from measured completeness and unsupported-claim deltas. A losing
 *   or mixed result is stated directly; the artifact cannot supply favorable authored prose.
 * - Wise green remains CTA-only. Statistical significance uses neutral treatment, while the
 *   synthetic/offline disclosure uses the semantic warning palette.
 */
import adr019Text from "../../../docs/architecture/adr/ADR-019-multi-agent-sar-drafting.md?raw";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { DataTable, type Column } from "../components/ui/DataTable";
import { Disclosure } from "../components/ui/Disclosure";
import { PageHeader } from "../components/ui/PageHeader";
import { StatTile } from "../components/ui/StatTile";
import { formatCurrency, formatDurationMs, formatPercent } from "../lib/format";
import {
  SAR_EVAL_VARIANTS,
  sarEvalDelta,
  type SarEvalArm,
  type SarEvalArmSummary,
  type SarEvalDelta,
  type SarEvalMetric,
  type SarEvalScenario,
  type SarEvalStudyData,
  type SarEvalTypology,
} from "../lib/sarEvalStudy";
import { paths } from "../lib/router";

function createAdrHref(): string {
  if (typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(new Blob([adr019Text], { type: "text/plain;charset=utf-8" }));
  }
  return `data:text/plain;charset=utf-8,${encodeURIComponent(adr019Text)}`;
}

export const ADR_019_HREF = createAdrHref();

interface MetricDefinition {
  metric: SarEvalMetric;
  label: string;
  better: "higher" | "lower";
}

interface MetricRow extends MetricDefinition {
  singleWriter: number;
  multiAgent: number;
  delta: SarEvalDelta;
}

const QUALITY_METRICS: readonly MetricDefinition[] = [
  { metric: "completenessRate", label: "FinCEN narrative completeness", better: "higher" },
  { metric: "unsupportedClaims", label: "Unsupported claims", better: "lower" },
];

const PROGRAMMATIC_METRICS: readonly MetricDefinition[] = [
  { metric: "citationPrecision", label: "Citation precision", better: "higher" },
  { metric: "citationRecall", label: "Citation recall", better: "higher" },
  { metric: "fabricatedCitationCount", label: "Fabricated citations", better: "lower" },
  { metric: "costUsd", label: "Cost per narrative", better: "lower" },
  { metric: "latencyMs", label: "Latency per narrative", better: "lower" },
  { metric: "modelCalls", label: "Model calls per narrative", better: "lower" },
];

const VARIANT_LABELS: Record<(typeof SAR_EVAL_VARIANTS)[number], string> = {
  clean: "Clean",
  thin_evidence: "Thin evidence",
  conflicting_evidence: "Conflicting evidence",
  citation_bait: "Citation bait",
};

const TYPOLOGY_LABELS: Record<SarEvalTypology, string> = {
  structuring: "Structuring",
  high_risk_wire: "High-risk wire",
  rapid_movement: "Rapid movement",
  funnel_account: "Funnel account",
  mule_velocity: "Mule velocity",
  round_amount_layering: "Round-amount layering",
  crypto_off_ramp: "Crypto off-ramp",
  shell_company_transfer: "Shell-company transfer",
};

function armLabel(arm: SarEvalArm): string {
  return arm === "single_writer" ? "Single-writer" : "Multi-agent";
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function metricValue(metric: SarEvalMetric, value: number): string {
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

function metricDelta(metric: SarEvalMetric, value: number): string {
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

function interval(delta: SarEvalDelta): string {
  return `[${metricDelta(delta.metric, delta.ciLower)}, ${metricDelta(delta.metric, delta.ciUpper)}]`;
}

function summaryFor(data: SarEvalStudyData, arm: SarEvalArm): SarEvalArmSummary {
  const summary = data.summary.arms.find((candidate) => candidate.arm === arm);
  if (!summary) {
    throw new Error(`validated SAR evaluation is missing ${arm}`);
  }
  return summary;
}

function rowsFor(
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

const METRIC_COLUMNS: Column<MetricRow>[] = [
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

const SCENARIO_COLUMNS: Column<SarEvalScenario>[] = [
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

interface AgreementRow {
  id: string;
  label: string;
  singleWriter: number;
  multiAgent: number;
}

const AGREEMENT_COLUMNS: Column<AgreementRow>[] = [
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

export interface SarEvalStudyProps {
  data: SarEvalStudyData;
}

export function SarEvalStudy({ data }: SarEvalStudyProps) {
  const singleWriter = summaryFor(data, "single_writer");
  const multiAgent = summaryFor(data, "multi_agent");
  const qualityRows = rowsFor(data, singleWriter, multiAgent, QUALITY_METRICS);
  const programmaticRows = rowsFor(data, singleWriter, multiAgent, PROGRAMMATIC_METRICS);
  const typologyCount = new Set(data.scenarios.map((scenario) => scenario.typology)).size;
  const completenessDelta = sarEvalDelta(data, "completenessRate");
  const unsupportedDelta = sarEvalDelta(data, "unsupportedClaims");
  const citationPrecisionDelta = sarEvalDelta(data, "citationPrecision");
  const costDelta = sarEvalDelta(data, "costUsd");
  const agreementRows: AgreementRow[] = [
    {
      id: "elements",
      label: "FinCEN element pass/fail",
      singleWriter: singleWriter.elementAgreement,
      multiAgent: multiAgent.elementAgreement,
    },
    {
      id: "unsupported-count",
      label: "Unsupported-claim count",
      singleWriter: singleWriter.unsupportedClaimCountAgreement,
      multiAgent: multiAgent.unsupportedClaimCountAgreement,
    },
    {
      id: "unsupported-spans",
      label: "Unsupported-claim spans",
      singleWriter: singleWriter.unsupportedClaimSpanAgreement,
      multiAgent: multiAgent.unsupportedClaimSpanAgreement,
    },
  ];

  return (
    <div className="gap-2xl flex flex-col">
      <PageHeader
        title="Multi-agent SAR drafting study"
        description="A paired offline evaluation of the shipped single-writer and bounded multi-agent workflows across adversarial synthetic scenarios."
      />

      <div
        role="note"
        className="gap-sm border-warning-deep/30 bg-warning/10 p-xl flex flex-col rounded-xl border"
      >
        <p className="text-body-sm text-ink font-semibold">
          Public synthetic offline study — not live tenant data.
        </p>
        <p className="text-body-sm text-body">
          This page renders one committed aggregate artifact. It makes no backend or provider call
          and contains no real PHI. Results support a drafting decision; they never approve a SAR.{" "}
          <a
            href={ADR_019_HREF}
            target="_blank"
            rel="noreferrer"
            className="text-ink font-semibold underline"
          >
            ADR-019 · Multi-agent SAR drafting
          </a>
        </p>
      </div>

      <p className="text-body-md text-ink font-semibold" data-testid="study-finding">
        {sarEvalHeadline(data)}
      </p>

      <div className="gap-lg grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Completeness paired delta"
          value={metricDelta(completenessDelta.metric, completenessDelta.pointEstimate)}
          hint={`multi-agent − single-writer · BCa 95% CI ${interval(completenessDelta)}`}
        />
        <StatTile
          label="Unsupported-claims paired delta"
          value={metricDelta(unsupportedDelta.metric, unsupportedDelta.pointEstimate)}
          hint={`multi-agent − single-writer · BCa 95% CI ${interval(unsupportedDelta)}`}
        />
        <StatTile
          label="Citation-precision paired delta"
          value={metricDelta(citationPrecisionDelta.metric, citationPrecisionDelta.pointEstimate)}
          hint={`multi-agent − single-writer · BCa 95% CI ${interval(citationPrecisionDelta)}`}
        />
        <StatTile
          label="Cost paired delta"
          value={metricDelta(costDelta.metric, costDelta.pointEstimate)}
          hint={`multi-agent − single-writer · BCa 95% CI ${interval(costDelta)}`}
        />
      </div>

      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Judge-scored quality</h2>
          <p className="text-body-sm text-body">
            The cross-family judge scored only unsupported claims and completeness across who, what,
            when, where, and why. Delta is always multi-agent minus single-writer.
          </p>
        </div>
        <DataTable
          caption="Judge-scored quality comparison"
          columns={METRIC_COLUMNS}
          rows={qualityRows}
          rowKey={(row) => row.metric}
        />
      </Card>

      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Programmatic metrics</h2>
          <p className="text-body-sm text-body">
            Citation ids use a closed vocabulary. Latency is the persisted investigation
            created-to-updated duration; cost and model calls are programmatic provenance attached
            to each API result, not judge interpretation.
          </p>
        </div>
        <DataTable
          caption="Programmatic evaluation metrics"
          columns={METRIC_COLUMNS}
          rows={programmaticRows}
          rowKey={(row) => row.metric}
        />
      </Card>

      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Per-scenario paired results</h2>
          <p className="text-body-sm text-body">
            Every deterministic typology/variant pair is shown. Each cell keeps the single-writer
            and multi-agent measurements together for direct comparison.
          </p>
        </div>
        <DataTable
          caption="Per-scenario paired results"
          columns={SCENARIO_COLUMNS}
          rows={data.scenarios}
          rowKey={(row) => row.scenarioId}
        />
      </Card>

      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Judge stability</h2>
          <p className="text-body-sm text-body">
            Inter-sample agreement across {data.judge.samplesPerNarrative} independent samples per
            narrative exposes the judge’s own consistency. Overall agreement is the mean of the
            three measures below.
          </p>
        </div>
        <dl className="gap-lg grid grid-cols-1 sm:grid-cols-2">
          <StatTile
            as="dl"
            label="Single-writer agreement"
            value={formatPercent(singleWriter.agreement)}
            emphasis="md"
          />
          <StatTile
            as="dl"
            label="Multi-agent agreement"
            value={formatPercent(multiAgent.agreement)}
            emphasis="md"
          />
        </dl>
        <DataTable
          caption="Judge inter-sample agreement by measure"
          columns={AGREEMENT_COLUMNS}
          rows={agreementRows}
          rowKey={(row) => row.id}
        />
      </Card>

      <Card className="gap-lg flex flex-col">
        <div className="gap-xs flex flex-col">
          <h2 className="text-display-xs text-ink">Protocol & provenance</h2>
          <p className="text-body-sm text-body">
            {data.scenarioCount} paired scenarios cover {typologyCount} typologies across four
            deterministic variants. Publication binds this projection to the full report by SHA-256.
          </p>
        </div>

        <dl className="gap-lg grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile as="dl" label="Run" value={data.runId} emphasis="md" />
          <StatTile as="dl" label="Seed" value={data.seed} emphasis="md" />
          <StatTile
            as="dl"
            label="Bootstrap"
            value={data.bootstrapResamples.toLocaleString()}
            hint="BCa resamples"
            emphasis="md"
          />
          <StatTile
            as="dl"
            label="Judge samples"
            value={data.judge.samplesPerNarrative}
            hint="per narrative"
            emphasis="md"
          />
        </dl>

        <div role="group" className="gap-sm flex flex-wrap" aria-label="Scenario variants">
          {SAR_EVAL_VARIANTS.map((variant) => (
            <Badge key={variant} tone="neutral">
              {VARIANT_LABELS[variant]}
            </Badge>
          ))}
        </div>

        <dl className="gap-md bg-canvas-soft p-lg grid grid-cols-1 rounded-lg sm:grid-cols-2">
          <div className="gap-xxs flex flex-col">
            <dt className="text-caption text-mute">Judge model</dt>
            <dd className="text-body-sm text-ink font-semibold">{data.judge.modelId}</dd>
            <dd className="text-caption text-body">Family: {data.judge.modelFamily}</dd>
          </div>
          <div className="gap-xxs flex flex-col">
            <dt className="text-caption text-mute">Judge prompt</dt>
            <dd className="text-body-sm text-ink font-semibold">{data.judge.promptVersion}</dd>
            <dd className="text-caption text-body break-all font-mono">{data.judge.promptHash}</dd>
          </div>
          <div className="gap-xxs flex flex-col">
            <dt className="text-caption text-mute">Blinding</dt>
            <dd className="text-body-sm text-ink">Blind; A/B order randomized per scenario</dd>
          </div>
          <div className="gap-xxs flex flex-col">
            <dt className="text-caption text-mute">Report binding</dt>
            <dd className="text-caption text-body break-all font-mono">{data.reportSha256}</dd>
          </div>
        </dl>

        <div className="gap-sm flex flex-col">
          {data.armProvenance.map((provenance) => (
            <Disclosure
              key={provenance.arm}
              summary={
                <span className="text-body-sm text-ink font-semibold">
                  {armLabel(provenance.arm)} provenance
                </span>
              }
            >
              <dl className="gap-md grid grid-cols-1 sm:grid-cols-2">
                <div className="gap-xxs flex flex-col">
                  <dt className="text-caption text-mute">Writer model</dt>
                  <dd className="text-body-sm text-body break-all">
                    {provenance.writerModelId} ({provenance.writerModelFamily})
                  </dd>
                </div>
                <div className="gap-xxs flex flex-col">
                  <dt className="text-caption text-mute">All workflow models</dt>
                  <dd className="text-body-sm text-body break-all">
                    {provenance.modelIds.join(", ")}
                  </dd>
                </div>
                <div className="gap-xxs flex flex-col">
                  <dt className="text-caption text-mute">Graph version</dt>
                  <dd className="text-body-sm text-body">{provenance.graphVersion ?? "None"}</dd>
                </div>
                <div className="gap-xxs flex flex-col">
                  <dt className="text-caption text-mute">Prompt versions</dt>
                  <dd className="text-body-sm text-body">{provenance.promptVersions.join(", ")}</dd>
                </div>
                <div className="gap-xxs flex flex-col">
                  <dt className="text-caption text-mute">Prompt hashes</dt>
                  {provenance.promptHashes.map((hash) => (
                    <dd key={hash} className="text-caption text-body break-all font-mono">
                      {hash}
                    </dd>
                  ))}
                </div>
              </dl>
            </Disclosure>
          ))}
        </div>
      </Card>
    </div>
  );
}

export const SAR_EVAL_STUDY_PATH = paths.researchMultiAgentSar;
