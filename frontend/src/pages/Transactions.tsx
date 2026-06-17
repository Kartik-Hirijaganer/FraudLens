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
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Select";
import {
  apiClient,
  type ApiClient,
  type ModelVersionListResponse,
  type TransactionResponse,
} from "../lib/api";
import { formatCurrency, formatDateTime, humanize } from "../lib/format";
import { riskTone } from "../lib/risk";
import { navigate, paths } from "../lib/router";
import { notify, notifyError } from "../lib/toast";
import { useAsync } from "../lib/useAsync";

interface TransactionsData {
  transactions: TransactionResponse[];
  models: ModelVersionListResponse;
}

const RISK_OPTIONS = [
  { value: "", label: "All risk bands" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "critical", label: "Critical" },
];

interface TransactionsProps {
  client?: ApiClient;
}

export function Transactions({ client = apiClient }: TransactionsProps) {
  const [riskBand, setRiskBand] = useState("");
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
      <header className="gap-sm bg-canvas-soft p-3xl flex flex-col rounded-xl">
        <h1 className="text-display-md text-ink">Transactions</h1>
        <p className="text-body-lg text-body">
          Import flagged transactions and start an investigation.
        </p>
      </header>
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
          <div className="grow">
            <Select
              label="Filter by risk band"
              options={RISK_OPTIONS}
              value={riskBand}
              onChange={(event) => setRiskBand(event.target.value)}
            />
          </div>
        </div>
        <AsyncBoundary state={state}>
          {(data) => (
            <div className="gap-lg flex flex-col">
              <ModelSelector
                versions={data.models.versions}
                activeLabel={data.models.activeVersionLabel}
                value={override}
                onChange={setOverride}
              />
              {data.transactions.length === 0 ? (
                <EmptyState
                  title="No transactions"
                  description="Import a CSV to start investigating."
                />
              ) : (
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="text-caption text-mute">
                      <th scope="col" className="px-lg py-md font-semibold">
                        External id
                      </th>
                      <th scope="col" className="px-lg py-md font-semibold">
                        Amount
                      </th>
                      <th scope="col" className="px-lg py-md font-semibold">
                        Country
                      </th>
                      <th scope="col" className="px-lg py-md font-semibold">
                        Channel
                      </th>
                      <th scope="col" className="px-lg py-md font-semibold">
                        Risk
                      </th>
                      <th scope="col" className="px-lg py-md font-semibold">
                        Occurred
                      </th>
                      <th scope="col" className="px-lg py-md">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.transactions.map((transaction) => (
                      <tr key={transaction.transactionId} className="border-canvas-soft border-t">
                        <td className="px-lg py-md text-body-sm text-ink">
                          {transaction.externalId}
                        </td>
                        <td className="px-lg py-md text-body-sm text-ink">
                          {formatCurrency(transaction.amount, transaction.currency)}
                        </td>
                        <td className="px-lg py-md text-body-sm text-body">
                          {transaction.country}
                        </td>
                        <td className="px-lg py-md text-body-sm text-body">
                          {humanize(transaction.channel)}
                        </td>
                        <td className="px-lg py-md">
                          {transaction.riskBand ? (
                            <Badge tone={riskTone(transaction.riskBand)}>
                              {humanize(transaction.riskBand)}
                            </Badge>
                          ) : (
                            <span className="text-caption text-mute">unscored</span>
                          )}
                        </td>
                        <td className="px-lg py-md text-body-sm text-body">
                          {formatDateTime(transaction.occurredAt)}
                        </td>
                        <td className="px-lg py-md">
                          <Button
                            onClick={() => void investigate(transaction.transactionId)}
                            disabled={investigatingId !== null}
                          >
                            {investigatingId === transaction.transactionId
                              ? "Starting…"
                              : "Investigate"}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </section>
  );
}
