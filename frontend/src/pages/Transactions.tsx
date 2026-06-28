/**
 * Summary: The transactions page (plan §16 Phase 11; FR-1 ingest, the entry to the
 * investigate flow). It imports a CSV (the masked-only ingest endpoint), lists the
 * agency's transactions with a risk-band filter, lets the analyst pick a model override
 * via the ModelSelector, and starts an investigation per row — navigating to the live
 * Investigation page on the returned runId. Outcomes surface as toasts; loading/empty/
 * error/retry flow through AsyncBoundary.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Transactions: render the import control, filter, model selector, and table.
 *
 * Notes:
 * - The CSV is read in-browser and posted as text/csv; the in-flight Investigate button is
 * disabled to guard against double-submits.
 */
import { useCallback, useState, type ChangeEvent } from "react";

import { ModelSelector } from "../components/ModelSelector";
import { RiskDot } from "../components/RiskDot";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { DataTable, type Column } from "../components/ui/DataTable";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { TextInput } from "../components/ui/TextInput";
import {
  apiClient,
  type ApiClient,
  type ModelVersionListResponse,
  type TransactionResponse,
} from "../lib/api";
import { formatCurrency, formatDateTime, humanize } from "../lib/format";
import { RISK_BAND_OPTIONS } from "../lib/options";
import { navigate, paths } from "../lib/router";
import { notify, notifyError } from "../lib/toast";
import { useAsync } from "../lib/useAsync";

interface TransactionsData {
  transactions: TransactionResponse[];
  models: ModelVersionListResponse;
}

interface TransactionsProps {
  client?: ApiClient;
}

export function Transactions({ client = apiClient }: TransactionsProps) {
  const [riskBand, setRiskBand] = useState("");
  const [search, setSearch] = useState("");
  const [override, setOverride] = useState<string | undefined>(undefined);
  const [investigatingId, setInvestigatingId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<TransactionsData> => {
    const [transactions, models] = await Promise.all([
      client.listTransactions({ riskBand: riskBand || undefined, limit: 50 }),
      client.listModelVersions(),
    ]);
    return { transactions: transactions.transactions, models };
  }, [client, riskBand]);
  const state = useAsync(load, [client, riskBand]);

  async function investigate(transactionId: string): Promise<void> {
    setInvestigatingId(transactionId);
    try {
      const result = await client.startInvestigation({ transactionId, modelOverride: override });
      navigate(paths.investigation(result.runId));
    } catch (caught) {
      notifyError(caught);
      setInvestigatingId(null);
    }
  }

  async function importCsv(file: File): Promise<void> {
    try {
      const result = await client.uploadCsv(await file.text());
      notify({
        tone: "positive",
        title: "Import complete",
        description: `${result.accepted} added · ${result.duplicates} duplicate · ${result.rejected} rejected`,
      });
      state.reload();
    } catch (caught) {
      notifyError(caught);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    if (file) {
      void importCsv(file);
    }
    event.target.value = "";
  }

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Transactions"
        description="Import flagged transactions and start an investigation."
      />
      <Card className="gap-lg flex flex-col">
        <div className="gap-lg flex flex-wrap items-end">
          <div className="gap-xs flex flex-col">
            <label htmlFor="csv-upload" className="text-body-sm text-body">
              Import transactions (CSV)
            </label>
            <input
              id="csv-upload"
              type="file"
              accept=".csv,text/csv"
              onChange={onFileChange}
              className="text-body-sm text-ink"
            />
          </div>
          <div className="gap-xs flex grow flex-col">
            <span className="text-body-sm text-body">Filter by risk band</span>
            <SegmentedControl
              ariaLabel="Filter by risk band"
              options={RISK_BAND_OPTIONS}
              value={riskBand}
              onChange={setRiskBand}
            />
          </div>
        </div>
        <AsyncBoundary state={state}>
          {(data) => {
            const filteredTransactions = filterTransactions(data.transactions, search);
            const columns = transactionColumns(investigatingId, investigate);
            return (
              <div className="gap-lg flex flex-col">
                <ModelSelector
                  versions={data.models.versions}
                  activeLabel={data.models.activeVersionLabel}
                  value={override}
                  onChange={setOverride}
                />
                <TextInput
                  label="Search transactions"
                  placeholder="Search by ID, amount, or counterparty…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
                <DataTable
                  caption="Transactions"
                  columns={columns}
                  rows={filteredTransactions}
                  rowKey={(transaction) => transaction.transactionId}
                  empty={
                    <EmptyState
                      title={data.transactions.length === 0 ? "No transactions" : "No matches"}
                      description={
                        data.transactions.length === 0
                          ? "Import a CSV to start investigating."
                          : "Adjust the search query or risk filter."
                      }
                    />
                  }
                />
              </div>
            );
          }}
        </AsyncBoundary>
      </Card>
    </section>
  );
}

function filterTransactions(
  transactions: TransactionResponse[],
  search: string,
): TransactionResponse[] {
  const query = search.trim().toLowerCase();
  if (!query) {
    return transactions;
  }
  return transactions.filter((transaction) =>
    [
      transaction.externalId,
      transaction.amount,
      transaction.currency,
      transaction.originAccount,
      transaction.destAccount,
      transaction.channel,
      transaction.country,
      transaction.riskBand ?? "unscored",
    ]
      .join(" ")
      .toLowerCase()
      .includes(query),
  );
}

function transactionColumns(
  investigatingId: string | null,
  investigate: (transactionId: string) => Promise<void>,
): Column<TransactionResponse>[] {
  return [
    {
      id: "externalId",
      header: "External id",
      cell: (transaction) => (
        <div className="gap-xxs flex flex-col">
          <span className="text-ink">{transaction.externalId}</span>
          <span className="text-caption text-mute">
            {transaction.originAccount} → {transaction.destAccount}
          </span>
        </div>
      ),
    },
    {
      id: "amount",
      header: "Amount",
      cell: (transaction) => (
        <span className="text-ink">{formatCurrency(transaction.amount, transaction.currency)}</span>
      ),
    },
    {
      id: "country",
      header: "Country",
      cell: (transaction) => <span className="text-body">{transaction.country}</span>,
    },
    {
      id: "channel",
      header: "Channel",
      cell: (transaction) => <span className="text-body">{humanize(transaction.channel)}</span>,
    },
    {
      id: "risk",
      header: "Risk",
      cell: (transaction) => <RiskDot band={transaction.riskBand} showLabel />,
    },
    {
      id: "occurred",
      header: "Occurred",
      cell: (transaction) => (
        <span className="text-body">{formatDateTime(transaction.occurredAt)}</span>
      ),
    },
    {
      id: "action",
      header: "Actions",
      srOnlyHeader: true,
      cell: (transaction) => (
        <Button
          onClick={() => void investigate(transaction.transactionId)}
          disabled={investigatingId !== null}
        >
          {investigatingId === transaction.transactionId ? "Starting…" : "Investigate"}
        </Button>
      ),
    },
  ];
}
