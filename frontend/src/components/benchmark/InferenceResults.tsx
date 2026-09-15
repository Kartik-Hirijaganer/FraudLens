/**
 * Summary: Measured BF16-versus-AWQ inference view with provenance, comparisons, accessible
 * charts/table alternatives, deterministic quality deltas, and reproduction commands.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - InferenceResults: render one validated aggregate vLLM benchmark artifact.
 *
 * Notes:
 * - The headline is publisher-derived from measurements; this component authors no speed claim.
 */
import { Badge } from "../ui/Badge";
import { DataTable, type Column } from "../ui/DataTable";
import { Disclosure } from "../ui/Disclosure";
import { StatTile } from "../ui/StatTile";
import { formatPercent, humanize } from "../../lib/format";
import type { VllmArm, VllmBenchmarkData, VllmLevel, VllmQuality } from "../../lib/vllmBenchmark";

interface InferenceResultsProps {
  data: VllmBenchmarkData;
}

interface LevelRow extends VllmLevel {
  arm: string;
}

const LEVEL_COLUMNS: Column<LevelRow>[] = [
  { id: "arm", header: "Arm", cell: (row) => row.arm.toUpperCase() },
  { id: "c", header: "Concurrency", cell: (row) => row.concurrency, align: "right" },
  {
    id: "p95",
    header: "p95 latency",
    cell: (row) => `${row.latencyP95Ms.toFixed(0)} ms`,
    align: "right",
  },
  {
    id: "ttft",
    header: "TTFT p95",
    cell: (row) => `${row.ttftP95Ms.toFixed(0)} ms`,
    align: "right",
  },
  {
    id: "rps",
    header: "Requests/s",
    cell: (row) => row.requestsPerSecond.toFixed(2),
    align: "right",
  },
  {
    id: "tokens",
    header: "Output tok/s",
    cell: (row) => row.generatedTokensPerSecond.toFixed(1),
    align: "right",
  },
  {
    id: "useful",
    header: "Useful drafts/s",
    cell: (row) => row.usefulDraftsPerSecond.toFixed(2),
    align: "right",
  },
  {
    id: "gpu",
    header: "GPU mean",
    cell: (row) =>
      row.telemetry.gpuUtilizationMeanPct === null
        ? "—"
        : `${row.telemetry.gpuUtilizationMeanPct.toFixed(1)}%`,
    align: "right",
  },
  {
    id: "kv",
    header: "KV peak",
    cell: (row) =>
      row.telemetry.kvCachePeakPct === null ? "—" : `${row.telemetry.kvCachePeakPct.toFixed(1)}%`,
    align: "right",
  },
  {
    id: "cost",
    header: "Cost / 1K",
    cell: (row) => `$${row.costPer1000DraftsUsd.toFixed(3)}`,
    align: "right",
  },
];

const QUALITY_METRICS: Array<{
  key: keyof VllmQuality;
  label: string;
  deltaKey?: string;
  rate?: boolean;
}> = [
  { key: "schemaValidRate", label: "Schema valid", deltaKey: "schema_valid_rate", rate: true },
  {
    key: "referenceValidity",
    label: "Reference validity",
    deltaKey: "reference_validity",
    rate: true,
  },
  { key: "citationRecall", label: "Citation recall", deltaKey: "citation_recall", rate: true },
  {
    key: "requiredFactCoverage",
    label: "Required-fact coverage",
    deltaKey: "required_fact_coverage",
    rate: true,
  },
  {
    key: "abstentionCorrectness",
    label: "Abstention correctness",
    deltaKey: "abstention_correctness",
    rate: true,
  },
  { key: "truncationRate", label: "Truncation rate", deltaKey: "truncation_rate", rate: true },
  { key: "fabricatedReferenceAttempts", label: "Fabricated reference attempts" },
  { key: "unsupportedClaimFlags", label: "Unsupported-claim flags" },
];

function topLevel(arm: VllmArm): VllmLevel {
  return arm.levels.reduce((best, item) => (item.concurrency > best.concurrency ? item : best));
}

function throughputPath(arm: VllmArm, maximum: number): string {
  return arm.levels
    .map((level, index) => {
      const x = 24 + index * 126;
      const y = 92 - (level.generatedTokensPerSecond / maximum) * 72;
      return `${index === 0 ? "M" : "L"} ${x} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function metricValue(quality: VllmQuality, key: keyof VllmQuality, rate = false): string {
  const value = quality[key];
  return rate ? formatPercent(value) : value.toLocaleString();
}

export function InferenceResults({ data }: InferenceResultsProps) {
  const [bf16, awq] = data.arms;
  const bf16Top = topLevel(bf16);
  const awqTop = topLevel(awq);
  const rows: LevelRow[] = data.arms.flatMap((arm) =>
    arm.levels.map((level) => ({ ...level, arm: arm.arm })),
  );
  const throughputMax = Math.max(...rows.map((level) => level.generatedTokensPerSecond), 1);
  const maxWeight = Math.max(bf16.server.weightMemoryGib, awq.server.weightMemoryGib);
  return (
    <div className="gap-xl flex flex-col">
      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <div className="gap-sm flex flex-wrap items-center">
          <Badge tone={data.acceptanceMet ? "positive" : "warning"}>
            {data.acceptanceMet ? "Acceptance met" : "Acceptance not met"}
          </Badge>
          <span className="text-caption text-body">
            {data.runId} · {awq.server.provider} {awq.server.sku} · vLLM {awq.server.vllmVersion}
          </span>
        </div>
        <p data-testid="study-finding" className="text-body-lg text-ink font-semibold">
          {data.headline}
        </p>
        <p className="text-caption text-body">
          {data.measuredCases.toLocaleString()} synthetic cases per level ·{" "}
          {awq.server.purchaseOption}
          {" · "}
          <a
            className="underline"
            href="https://github.com/Kartik-Hirijaganer/FraudLens/blob/main/docs/architecture/adr/ADR-020-vllm-awq-self-hosted-sar-inference.md"
          >
            ADR-020
          </a>
        </p>
      </section>

      <div className="gap-md grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Model-weight memory"
          value={`${bf16.server.weightMemoryGib.toFixed(1)} → ${awq.server.weightMemoryGib.toFixed(1)} GiB`}
          hint={`${formatPercent(data.weightMemoryReduction)} reduction`}
        />
        <StatTile
          label={`p95 latency · c${awqTop.concurrency}`}
          value={`${bf16Top.latencyP95Ms.toFixed(0)} / ${awqTop.latencyP95Ms.toFixed(0)} ms`}
          hint="BF16 / AWQ"
        />
        <StatTile
          label={`Output tok/s · c${awqTop.concurrency}`}
          value={`${bf16Top.generatedTokensPerSecond.toFixed(0)} / ${awqTop.generatedTokensPerSecond.toFixed(0)}`}
          hint="BF16 / AWQ"
        />
        <StatTile
          label="AWQ cost / 1K drafts"
          value={`$${awqTop.costPer1000DraftsUsd.toFixed(3)}`}
          hint={`${awq.server.purchaseOption} at $${awq.server.hourlyRateUsd.toFixed(2)}/hour`}
        />
      </div>

      <section className="gap-lg bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Memory and throughput</h2>
        <div className="gap-xl grid grid-cols-1 lg:grid-cols-2">
          <figure className="gap-sm flex flex-col">
            <figcaption className="text-body-sm text-ink font-semibold">Weight memory</figcaption>
            <svg viewBox="0 0 300 110" role="img" aria-label="BF16 and AWQ weight-memory bars">
              <rect
                x="28"
                y="22"
                width={(bf16.server.weightMemoryGib / maxWeight) * 238}
                height="24"
                rx="8"
                className="fill-accent-cyan"
              />
              <rect
                x="28"
                y="66"
                width={(awq.server.weightMemoryGib / maxWeight) * 238}
                height="24"
                rx="8"
                className="fill-accent-orange"
              />
              <text x="4" y="39" className="fill-ink text-caption">
                BF16
              </text>
              <text x="4" y="83" className="fill-ink text-caption">
                AWQ
              </text>
            </svg>
          </figure>
          <figure className="gap-sm flex flex-col">
            <figcaption className="text-body-sm text-ink font-semibold">
              Output tokens / second
            </figcaption>
            <svg
              viewBox="0 0 300 110"
              role="img"
              aria-label="Throughput by concurrency for both arms"
            >
              <line x1="20" y1="92" x2="282" y2="92" className="stroke-mute" />
              <path
                d={throughputPath(bf16, throughputMax)}
                fill="none"
                strokeWidth="4"
                className="stroke-accent-cyan"
              />
              <path
                d={throughputPath(awq, throughputMax)}
                fill="none"
                strokeWidth="4"
                className="stroke-accent-orange"
              />
            </svg>
          </figure>
        </div>
        <p className="text-caption text-body">
          Cyan: BF16 · orange: AWQ. The table below is the accessible source for every plotted
          value.
        </p>
      </section>

      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Performance by concurrency</h2>
        <DataTable
          rows={rows}
          columns={LEVEL_COLUMNS}
          rowKey={(row) => `${row.arm}-${row.concurrency}`}
          caption="BF16 and AWQ latency, throughput, telemetry, and cost by concurrency"
        />
      </section>

      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Quality comparison</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="sr-only">Aggregate benchmark quality by arm</caption>
            <thead className="bg-canvas-soft text-caption text-body">
              <tr>
                <th className="px-lg py-md">Metric</th>
                <th className="px-lg py-md text-right">BF16</th>
                <th className="px-lg py-md text-right">AWQ</th>
                <th className="px-lg py-md text-right">AWQ delta</th>
              </tr>
            </thead>
            <tbody>
              {QUALITY_METRICS.map((metric) => {
                const delta = data.qualityDeltas.find((item) => item.metric === metric.deltaKey);
                return (
                  <tr key={metric.key} className="border-canvas-soft text-body-sm border-t">
                    <th className="px-lg py-md font-semibold">{metric.label}</th>
                    <td className="px-lg py-md text-right">
                      {metricValue(bf16.quality, metric.key, metric.rate)}
                    </td>
                    <td className="px-lg py-md text-right">
                      {metricValue(awq.quality, metric.key, metric.rate)}
                    </td>
                    <td className="px-lg py-md text-right">
                      {delta
                        ? `${delta.deltaPercentagePoints.toFixed(2)} pp${delta.warning ? " · warning" : ""}`
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="gap-lg bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Protocol and provenance</h2>
        <dl className="gap-lg grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile as="dl" label="GPU" value={awq.server.gpuName} emphasis="md" />
          <StatTile as="dl" label="Driver" value={awq.server.driverVersion} emphasis="md" />
          <StatTile
            as="dl"
            label="Image digest"
            value={awq.server.imageDigest.slice(0, 19)}
            emphasis="md"
          />
          <StatTile
            as="dl"
            label="KV capacity"
            value={awq.server.kvCacheTokens.toLocaleString()}
            emphasis="md"
          />
          <StatTile as="dl" label="Region" value={awq.server.region} emphasis="md" />
          <StatTile
            as="dl"
            label="Price verified"
            value={awq.server.priceVerifiedAt}
            emphasis="md"
          />
          <StatTile
            as="dl"
            label="Report SHA"
            value={data.reportSha256.slice(0, 16)}
            emphasis="md"
          />
          <StatTile as="dl" label="Model" value={humanize(awq.arm)} emphasis="md" />
        </dl>
        <a className="text-body-sm text-ink underline" href={awq.server.priceSourceUrl}>
          Provider price source
        </a>
        <Disclosure summary="Reproduce this benchmark">
          <div className="gap-md flex flex-col">
            <code className="bg-canvas p-md text-caption text-body overflow-x-auto rounded-lg">
              PROFILE=full SOURCE=ibm-final-test make vllm-bench-cases &amp;&amp; RUN=&lt;run-id&gt;
              ARM=bf16 PROFILE=full SOURCE=ibm-final-test make vllm-bench-run &amp;&amp;
              RUN=&lt;run-id&gt; ARM=awq PROFILE=full SOURCE=ibm-final-test make vllm-bench-run
              &amp;&amp; make vllm-bench-validate
            </code>
            <a
              className="text-body-sm text-ink underline"
              href="https://github.com/Kartik-Hirijaganer/FraudLens/releases"
            >
              Download the aggregate release assets
            </a>
          </div>
        </Disclosure>
      </section>
    </div>
  );
}
