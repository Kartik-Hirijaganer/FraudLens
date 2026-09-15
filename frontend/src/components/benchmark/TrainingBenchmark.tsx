/**
 * Summary: Training-at-scale view backed only by the published IBM aggregate study projection.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - TrainingBenchmark: render reconciliation, holdout metrics, provenance, and attribution.
 *
 * Notes:
 * - Missing Phase 6 evidence is explicit; no placeholder measurement is presented as observed.
 */
import { EmptyState } from "../feedback/EmptyState";
import { Badge } from "../ui/Badge";
import { DataTable, type Column } from "../ui/DataTable";
import { StatTile } from "../ui/StatTile";
import { formatPercent } from "../../lib/format";
import type { FullDataCandidate, FullDataTrainingData } from "../../lib/fullDataTraining";

interface TrainingBenchmarkProps {
  data: FullDataTrainingData | null;
}

const COLUMNS: Column<FullDataCandidate>[] = [
  {
    id: "candidate",
    header: "Candidate",
    cell: (row) => <span className="font-semibold">{row.candidate}</span>,
  },
  { id: "source", header: "Source", cell: (row) => row.source },
  {
    id: "sourceRows",
    header: "Source rows",
    cell: (row) => row.sourceRows.toLocaleString(),
    align: "right",
  },
  {
    id: "usableRows",
    header: "Usable",
    cell: (row) => row.usableRows.toLocaleString(),
    align: "right",
  },
  {
    id: "trainingRows",
    header: "Training",
    cell: (row) => row.trainingRows.toLocaleString(),
    align: "right",
  },
  {
    id: "evaluationRows",
    header: "Holdout",
    cell: (row) => row.evaluationRows.toLocaleString(),
    align: "right",
  },
  { id: "prAuc", header: "PR-AUC", cell: (row) => formatPercent(row.prAuc), align: "right" },
  {
    id: "baseline",
    header: "LR baseline",
    cell: (row) => formatPercent(row.baselinePrAuc),
    align: "right",
  },
  {
    id: "recall",
    header: "Recall @ budget",
    cell: (row) => formatPercent(row.recallAtBudget),
    align: "right",
  },
];

export function TrainingBenchmark({ data }: TrainingBenchmarkProps) {
  if (!data) {
    return (
      <EmptyState
        title="Measured full-data evidence pending"
        description="The 68.2M-source-row Azure experiment must finish, publish, validate, and tear down before this view can claim measured results."
      />
    );
  }
  const application = data.candidates.find((item) => item.candidate === data.applicationCandidate)!;
  return (
    <div className="gap-xl flex flex-col">
      <div className="gap-md grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Source rows" value={application.sourceRows.toLocaleString()} />
        <StatTile label="Usable rows" value={application.usableRows.toLocaleString()} />
        <StatTile label="Application PR-AUC" value={formatPercent(application.prAuc)} />
        <StatTile label="Recall at budget" value={formatPercent(application.recallAtBudget)} />
      </div>
      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <div className="gap-sm flex flex-wrap items-center">
          <h2 className="text-display-xs text-ink">Temporal holdout candidates</h2>
          <Badge tone="neutral">Pre-registered: {data.applicationCandidate}</Badge>
        </div>
        <p className="text-body-sm text-body">
          Thresholds are selected on calibration data; the final holdout remains untouched until
          evaluation. Source-row count is not the model-fitting count.
        </p>
        <DataTable
          rows={data.candidates}
          columns={COLUMNS}
          rowKey={(row) => row.candidate}
          caption="Full-data reconciliation and temporal holdout metrics"
        />
      </section>
      <section className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
        <h2 className="text-display-xs text-ink">Protocol and licence</h2>
        <dl className="gap-lg grid grid-cols-1 sm:grid-cols-3">
          <StatTile as="dl" label="Run" value={data.runId} emphasis="md" />
          <StatTile as="dl" label="Provenance" value={data.provenance} emphasis="md" />
          <StatTile
            as="dl"
            label="Candidate gates"
            value={application.gatesPassed ? "Passed" : "Not passed"}
            emphasis="md"
          />
        </dl>
        <p className="text-caption text-body">
          IBM AML transaction data is synthetic and used under its published licence; FraudLens
          publishes aggregate counts and metrics only.
        </p>
      </section>
    </div>
  );
}
