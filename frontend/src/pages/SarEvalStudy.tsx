/**
 * Summary: Authenticated page for the committed, public-synthetic multi-agent SAR study.
 *
 * Key classes:
 * - SarEvalStudyProps: validated study projection rendered by the page.
 *
 * Key functions:
 * - ADR_019_HREF: expose the canonical decision record.
 * - SarEvalStudy: render findings, comparisons, stability, and provenance.
 *
 * Notes:
 * - This page renders a committed aggregate artifact and performs no provider call.
 */
import adr019Text from "../../../docs/architecture/adr/ADR-019-multi-agent-sar-drafting.md?raw";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { DataTable } from "../components/ui/DataTable";
import { Disclosure } from "../components/ui/Disclosure";
import { PageHeader } from "../components/ui/PageHeader";
import { StatTile } from "../components/ui/StatTile";
import { formatPercent } from "../lib/format";
import { SAR_EVAL_VARIANTS, sarEvalDelta, type SarEvalStudyData } from "../lib/sarEvalStudy";
import {
  AGREEMENT_COLUMNS,
  METRIC_COLUMNS,
  SCENARIO_COLUMNS,
  type AgreementRow,
} from "./SarEvalStudyTables";
import {
  PROGRAMMATIC_METRICS,
  QUALITY_METRICS,
  VARIANT_LABELS,
  armLabel,
  interval,
  metricDelta,
  rowsFor,
  sarEvalHeadline,
  summaryFor,
} from "./sarEvalStudyMetrics";

export { sarEvalHeadline } from "./sarEvalStudyMetrics";

function createAdrHref(): string {
  if (typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(new Blob([adr019Text], { type: "text/plain;charset=utf-8" }));
  }
  return `data:text/plain;charset=utf-8,${encodeURIComponent(adr019Text)}`;
}

export const ADR_019_HREF = createAdrHref();

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
