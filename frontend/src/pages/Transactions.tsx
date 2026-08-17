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
 * - The risk filter is mirrored in the route as `?riskBand=`, so the dashboard's band chips deep
 *   link here and a filtered view is shareable; the page reads that param on entry and rewrites
 *   it whenever the filter changes.
 * - The cursor stack records each visited page's cursor so Prev pops and Next pushes; changing
 *   the filter or search resets it to the first page. The in-flight Investigate is guarded.
 * - Model override is admin-only: non-admin roles load transactions without calling the
 *   admin-only model registry and investigations use the active model implicitly.
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
import {
  formatCurrency,
  formatDateTime,
  formatMaskedAccount,
  formatModelVersion,
  formatTransactionRef,
} from "../lib/format";
import { RISK_BAND_OPTIONS } from "../lib/options";
import { navigate, paths, useHashRoute } from "../lib/router";
import { hasPermission, useSession } from "../lib/session";
import { notify, notifyError } from "../lib/toast";
import { useAsync } from "../lib/useAsync";

const PAGE_SIZE = 10;
const SEARCH_DEBOUNCE_MS = 300;

interface TransactionsData {
  transactions: TransactionResponse[];
  nextCursor: string | null;
  total: number;
  models: ModelVersionListResponse | null;
}

interface TransactionsProps {
  client?: ApiClient;
}

export function Transactions({ client = apiClient }: TransactionsProps) {
  const route = useHashRoute();
  const routeRiskBand = route.name === "transactions" ? (route.riskBand ?? "") : "";
  const [riskBand, setRiskBand] = useState(routeRiskBand);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [override, setOverride] = useState<string | undefined>(undefined);
  const [investigatingId, setInvestigatingId] = useState<string | null>(null);
  const session = useSession();
  const canIngestTransactions = hasPermission(session, "ingestTransactions");
  const canStartInvestigation = hasPermission(session, "startInvestigation");
  const canChooseModel = hasPermission(session, "manageAdmin");
  // A stack of page cursors, one per visited page (index 0 = the first page, no cursor).
  // Prev pops, Next pushes the server's nextCursor — so keyset paging works both ways.
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const pageIndex = cursorStack.length - 1;
  const cursor = cursorStack[pageIndex];

  // A `?riskBand=` deep link (a dashboard band chip, a shared URL, or Back) drives the filter.
  // `changeRiskBand` writes the same param, so after a click this simply re-affirms the value.
  useEffect(() => {
    setRiskBand(routeRiskBand);
    setCursorStack([undefined]);
  }, [routeRiskBand]);

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
    const page = await client.listTransactions({
      riskBand: riskBand || undefined,
      search: search || undefined,
      limit: PAGE_SIZE,
      cursor,
    });
    const models = canChooseModel ? await client.listModelVersions() : null;
    return {
      transactions: page.transactions,
      nextCursor: page.nextCursor,
      total: page.total,
      models,
    };
  }, [canChooseModel, client, riskBand, search, cursor]);
  const state = useAsync(load, [client, riskBand, search, cursor]);
  const loadMetrics = useCallback(() => client.getDashboardMetrics(), [client]);
  const metricsState = useAsync(loadMetrics, [client]);

  function changeRiskBand(next: string): void {
    setRiskBand(next);
    setCursorStack([undefined]); // a new filter restarts paging at the first page
    navigate(paths.transactionsByRiskBand(next)); // keep the URL shareable/bookmarkable
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
        description="Backend-persisted transactions are ready for review. Open one to run or inspect scoring."
        actions={canIngestTransactions ? <ImportButton onFileChange={onFileChange} /> : undefined}
      />
      <Card className="gap-lg flex flex-col">
        <div className="gap-md flex flex-col xl:flex-row xl:items-center xl:justify-between">
          <SearchInput value={searchInput} onChange={setSearchInput} />
          <div className="shrink-0">
            <SegmentedControl
              ariaLabel="Filter by risk band"
              options={RISK_BAND_OPTIONS}
              value={riskBand}
              onChange={changeRiskBand}
              size="sm"
            />
          </div>
        </div>
        <AsyncBoundary state={state}>
          {(data) => {
            const columns = transactionColumns(
              investigatingId,
              canStartInvestigation ? investigate : undefined,
            );
            const rangeEnd = pageIndex * PAGE_SIZE + data.transactions.length;
            return (
              <>
                <ProvenanceStrip
                  total={data.total}
                  activeModelLabel={metricsState.data?.modelHealth.activeVersionLabel ?? null}
                />
                {data.models ? (
                  <div className="max-w-sm">
                    <ModelSelector
                      versions={data.models.versions}
                      activeLabel={data.models.activeVersionLabel}
                      value={override}
                      onChange={setOverride}
                    />
                  </div>
                ) : null}
                <DataTable
                  caption="Transactions"
                  columns={columns}
                  rows={data.transactions}
                  rowKey={(transaction) => transaction.transactionId}
                  onRowClick={
                    canStartInvestigation
                      ? (transaction) => void investigate(transaction.transactionId)
                      : undefined
                  }
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
    <label className="bg-primary text-ink px-xl py-md text-button-md gap-sm hover:bg-primary-active inline-flex cursor-pointer items-center rounded-xl font-semibold transition-colors">
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
    <div className="relative w-full xl:min-w-0 xl:flex-1">
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
        placeholder="Search source ID, amount, or account"
        className="rounded-pill border-ink bg-canvas text-body-md text-ink placeholder:text-mute py-md pl-3xl pr-lg w-full border"
      />
    </div>
  );
}

function ProvenanceStrip({
  total,
  activeModelLabel,
}: {
  total: number;
  activeModelLabel: string | null;
}) {
  const modelLabel = activeModelLabel
    ? `${/ibm[-_]aml/i.test(activeModelLabel) ? "IBM AML-trained" : "Active"} model ${formatModelVersion(activeModelLabel)}`
    : "Backend scoring pipeline";
  return (
    <aside
      aria-label="Data provenance"
      className="bg-canvas-soft px-lg py-md gap-sm text-body-sm text-body flex flex-wrap items-center rounded-lg"
    >
      <span className="text-ink font-semibold">Data provenance</span>
      <span aria-hidden="true" className="text-mute">
        ·
      </span>
      <span>
        {total.toLocaleString()} backend-persisted synthetic{" "}
        {total === 1 ? "scenario" : "scenarios"}
      </span>
      <span aria-hidden="true" className="text-mute">
        ·
      </span>
      <span>{modelLabel}</span>
      <span aria-hidden="true" className="text-mute">
        ·
      </span>
      <span>Account identifiers masked</span>
    </aside>
  );
}

function transactionColumns(
  investigatingId: string | null,
  investigate?: (transactionId: string) => Promise<void>,
): Column<TransactionResponse>[] {
  const columns: Column<TransactionResponse>[] = [
    {
      id: "externalId",
      header: "Transaction",
      cell: (transaction) => (
        <div className="gap-xxs flex flex-col" title={`Source ID: ${transaction.externalId}`}>
          <span className="text-ink font-semibold">
            {formatTransactionRef(transaction.transactionId, transaction.occurredAt)}
          </span>
          <span className="text-caption text-mute">
            {transaction.channel.toUpperCase()} · {transaction.country}
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
      id: "risk",
      header: "Risk",
      cell: (transaction) => <RiskDot band={transaction.riskBand} showLabel />,
    },
    {
      id: "counterparty",
      header: "Account flow",
      cell: (transaction) => (
        <dl className="gap-x-sm gap-y-xxs grid grid-cols-[auto_1fr] items-center">
          <dt className="text-caption text-mute">To</dt>
          <dd className="text-body-sm text-ink font-semibold">
            {formatMaskedAccount(transaction.destAccount)}
          </dd>
          <dt className="text-caption text-mute">From</dt>
          <dd className="text-caption text-body">
            {formatMaskedAccount(transaction.originAccount)}
          </dd>
        </dl>
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
  ];

  if (!investigate) {
    return columns;
  }

  return [
    ...columns,
    {
      id: "action",
      header: "Investigate",
      srOnlyHeader: true,
      align: "right",
      cell: (transaction) => {
        const busy = investigatingId === transaction.transactionId;
        const displayRef = formatTransactionRef(transaction.transactionId, transaction.occurredAt);
        return (
          <button
            type="button"
            onClick={() => void investigate(transaction.transactionId)}
            disabled={investigatingId !== null}
            aria-label={`Investigate transaction ${displayRef}`}
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
