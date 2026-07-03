/**
 * Summary: The transactions page (plan §16 Phase 11; FR-1 ingest, the entry to the
 * investigate flow), redesigned to the dashboard chrome. It lists the agency's scored
 * transactions in a scannable card — pill search, a risk-band filter, and a compact model
 * selector — over a TRUE server-side keyset-paginated table (Prev/Next via a cursor stack,
 * newest first). Opening a row (or its chevron) starts an investigation and deep-links to
 * the live run. A design-system Import CSV control (the masked-only ingest endpoint) sits in
 * the header. Loading / empty / error+retry flow through AsyncBoundary; outcomes are toasts.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Transactions: render the import action, filters, model selector, table, and pagination.
 *
 * Notes:
 * - Paging, search, and the risk filter are all SERVER-side: each page is one keyset request
 *   (limit + cursor), search is a debounced `search` query, and the "Showing X–Y of Z" total
 *   comes from the list response so it stays exact under any filter without scanning client-side.
 * - The cursor stack records each visited page's cursor so Prev pops and Next pushes; changing
 *   the filter or search resets it to the first page. The in-flight Investigate is guarded.
 */
import { useCallback, useEffect, useState, type ChangeEvent } from "react";

import { ModelSelector } from "../components/ModelSelector";
import { RiskDot } from "../components/RiskDot";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Card } from "../components/ui/Card";
import { DataTable, type Column } from "../components/ui/DataTable";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import {
  apiClient,
  type ApiClient,
  type ModelVersionListResponse,
  type TransactionResponse,
} from "../lib/api";
import { formatCurrency, formatDateTime } from "../lib/format";
import { RISK_BAND_OPTIONS } from "../lib/options";
import { navigate, paths } from "../lib/router";
import { notify, notifyError } from "../lib/toast";
import { useAsync } from "../lib/useAsync";

const PAGE_SIZE = 10;
const SEARCH_DEBOUNCE_MS = 300;

interface TransactionsData {
  transactions: TransactionResponse[];
  nextCursor: string | null;
  total: number;
  models: ModelVersionListResponse;
}

interface TransactionsProps {
  client?: ApiClient;
}

export function Transactions({ client = apiClient }: TransactionsProps) {
  const [riskBand, setRiskBand] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [override, setOverride] = useState<string | undefined>(undefined);
  const [investigatingId, setInvestigatingId] = useState<string | null>(null);
  // A stack of page cursors, one per visited page (index 0 = the first page, no cursor).
  // Prev pops, Next pushes the server's nextCursor — so keyset paging works both ways.
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const pageIndex = cursorStack.length - 1;
  const cursor = cursorStack[pageIndex];

  // Debounce the search box, and reset to the first page when the applied query changes
  // (both state writes happen together so the list refetches once, not twice).
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setCursorStack([undefined]);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const load = useCallback(async (): Promise<TransactionsData> => {
    const [page, models] = await Promise.all([
      client.listTransactions({
        riskBand: riskBand || undefined,
        search: search || undefined,
        limit: PAGE_SIZE,
        cursor,
      }),
      client.listModelVersions(),
    ]);
    return {
      transactions: page.transactions,
      nextCursor: page.nextCursor,
      total: page.total,
      models,
    };
  }, [client, riskBand, search, cursor]);
  const state = useAsync(load, [client, riskBand, search, cursor]);

  function changeRiskBand(next: string): void {
    setRiskBand(next);
    setCursorStack([undefined]); // a new filter restarts paging at the first page
  }

  async function investigate(transactionId: string): Promise<void> {
    if (investigatingId !== null) {
      return;
    }
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
      setCursorStack([undefined]);
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
        description="Every transaction is scored the moment it lands. Search, filter, and open one to investigate."
        actions={<ImportButton onFileChange={onFileChange} />}
      />
      <Card className="gap-lg flex flex-col">
        <div className="gap-md flex flex-col lg:flex-row lg:items-center lg:justify-between">
          <SearchInput value={searchInput} onChange={setSearchInput} />
          <SegmentedControl
            ariaLabel="Filter by risk band"
            options={RISK_BAND_OPTIONS}
            value={riskBand}
            onChange={changeRiskBand}
          />
        </div>
        <AsyncBoundary state={state}>
          {(data) => {
            const columns = transactionColumns(investigatingId, investigate);
            const rangeEnd = pageIndex * PAGE_SIZE + data.transactions.length;
            return (
              <>
                <div className="max-w-sm">
                  <ModelSelector
                    versions={data.models.versions}
                    activeLabel={data.models.activeVersionLabel}
                    value={override}
                    onChange={setOverride}
                  />
                </div>
                <DataTable
                  caption="Transactions"
                  columns={columns}
                  rows={data.transactions}
                  rowKey={(transaction) => transaction.transactionId}
                  onRowClick={(transaction) => void investigate(transaction.transactionId)}
                  empty={
                    <EmptyState
                      title={search || riskBand ? "No matches" : "No transactions yet"}
                      description={
                        search || riskBand
                          ? "Try a different search term or risk filter."
                          : "Import a CSV to start scoring and investigating transactions."
                      }
                    />
                  }
                />
                {data.total > 0 ? (
                  <Pagination
                    total={data.total}
                    rangeStart={pageIndex * PAGE_SIZE + 1}
                    rangeEnd={rangeEnd}
                    hasPrev={pageIndex > 0}
                    hasNext={Boolean(data.nextCursor)}
                    onPrev={() => setCursorStack((stack) => stack.slice(0, -1))}
                    onNext={() =>
                      setCursorStack((stack) =>
                        data.nextCursor ? [...stack, data.nextCursor] : stack,
                      )
                    }
                  />
                ) : null}
              </>
            );
          }}
        </AsyncBoundary>
      </Card>
    </section>
  );
}

function ImportButton({
  onFileChange,
}: {
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label className="bg-canvas-soft text-ink px-xl py-md text-button-md gap-sm hover:bg-primary-neutral inline-flex cursor-pointer items-center rounded-xl font-semibold transition-colors">
      Import CSV
      <input
        type="file"
        accept=".csv,text/csv"
        onChange={onFileChange}
        className="sr-only"
        aria-label="Import CSV"
      />
    </label>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="relative w-full lg:max-w-xl">
      <label htmlFor="txn-search" className="sr-only">
        Search transactions
      </label>
      <span
        aria-hidden="true"
        className="text-mute left-lg pointer-events-none absolute inset-y-0 flex items-center"
      >
        <SearchIcon />
      </span>
      <input
        id="txn-search"
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Search by ID, amount, or counterparty"
        className="rounded-pill border-ink bg-canvas text-body-md text-ink placeholder:text-mute py-md pl-3xl pr-lg w-full border"
      />
    </div>
  );
}

function transactionColumns(
  investigatingId: string | null,
  investigate: (transactionId: string) => Promise<void>,
): Column<TransactionResponse>[] {
  return [
    {
      id: "externalId",
      header: "TXN ID",
      cell: (transaction) => (
        <span className="text-ink font-semibold">{transaction.externalId}</span>
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
      id: "risk",
      header: "Risk",
      cell: (transaction) => <RiskDot band={transaction.riskBand} showLabel />,
    },
    {
      id: "counterparty",
      header: "Counterparty",
      cell: (transaction) => (
        <div className="gap-xxs flex flex-col">
          <span className="text-ink">{transaction.destAccount}</span>
          <span className="text-caption text-mute">from {transaction.originAccount}</span>
        </div>
      ),
    },
    {
      id: "occurred",
      header: "Time",
      cell: (transaction) => (
        <span className="text-body whitespace-nowrap">
          {formatDateTime(transaction.occurredAt)}
        </span>
      ),
    },
    {
      id: "action",
      header: "Investigate",
      srOnlyHeader: true,
      align: "right",
      cell: (transaction) => {
        const busy = investigatingId === transaction.transactionId;
        return (
          <button
            type="button"
            onClick={() => void investigate(transaction.transactionId)}
            disabled={investigatingId !== null}
            aria-label={`Investigate transaction ${transaction.externalId}`}
            className="text-mute hover:text-ink disabled:opacity-50"
          >
            {busy ? <span className="text-body-sm">Starting…</span> : <ChevronRight />}
          </button>
        );
      },
    },
  ];
}

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="m9 6 6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
