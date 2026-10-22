/**
 * Summary: The transactions page (plan §16 Phase 11; FR-1 ingest, the entry to the
 * investigate flow), redesigned to the dashboard chrome. It lists the agency's scored
 * transactions in a scannable card — pill search, a risk-band filter, and a compact model
 * selector — over a TRUE server-side keyset-paginated table (Prev/Next via a cursor stack,
 * newest first). Opening a scored row opens its saved run; a separate Re-run action starts another investigation. A design-system Import CSV control (the masked-only ingest endpoint) sits in
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
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import { ModelSelector } from "../components/ModelSelector";
import { CsvValidationReport } from "../components/CsvValidationReport";
import { ScreeningStatus } from "../components/ScreeningStatus";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { DataTable } from "../components/ui/DataTable";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import {
  ApiError,
  apiClient,
  type ApiClient,
  type TransactionResponse,
  type CsvUploadResponse,
} from "../lib/api";
import { formatModelVersion } from "../lib/format";
import { RISK_BAND_OPTIONS } from "../lib/options";
import { navigate, paths, useHashRoute } from "../lib/router";
import { hasPermission, useSession } from "../lib/session";
import { notify, notifyError } from "../lib/toast";
import { useAsync } from "../lib/useAsync";
import { useScreeningRefresh } from "../lib/useScreeningRefresh";
import { transactionColumns } from "./transactionColumns";

const PAGE_SIZE = 10;
const SEARCH_DEBOUNCE_MS = 300;

interface TransactionsData {
  transactions: TransactionResponse[];
  nextCursor: string | null;
  total: number;
}

interface TransactionsProps {
  client?: ApiClient;
}

export function Transactions({ client = apiClient }: TransactionsProps) {
  const route = useHashRoute();
  const routeRiskBand = route.name === "transactions" ? (route.riskBand ?? "") : "";
  const [uploadReport, setUploadReport] = useState<CsvUploadResponse | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [riskBand, setRiskBand] = useState(routeRiskBand);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [override, setOverride] = useState<string | undefined>(undefined);
  const [investigatingId, setInvestigatingId] = useState<string | null>(null);
  const actionInFlight = useRef(false);
  const attempt = useRef<{ transactionId: string; modelOverride?: string; key: string } | null>(
    null,
  );
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
    const nextSearch = searchInput.trim();
    if (nextSearch === search) {
      return;
    }
    const timer = setTimeout(() => {
      setSearch(nextSearch);
      setCursorStack([undefined]);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, search]);

  const load = useCallback(async (): Promise<TransactionsData> => {
    const page = await client.listTransactions({
      riskBand: riskBand || undefined,
      search: search || undefined,
      limit: PAGE_SIZE,
      cursor,
    });
    return {
      transactions: page.transactions,
      nextCursor: page.nextCursor,
      total: page.total,
    };
  }, [client, riskBand, search, cursor]);
  const state = useAsync(load, [client, riskBand, search, cursor]);
  const loadModels = useCallback(
    () => (canChooseModel ? client.listModelVersions() : Promise.resolve(null)),
    [canChooseModel, client],
  );
  const modelsState = useAsync(loadModels, [canChooseModel, client]);
  const loadMetrics = useCallback(() => client.getDashboardMetrics(), [client]);
  const metricsState = useAsync(loadMetrics, [client]);
  const screening = useScreeningRefresh(client, () => {
    state.reload();
    metricsState.reload();
  });

  function changeRiskBand(next: string): void {
    setRiskBand(next);
    setCursorStack([undefined]); // a new filter restarts paging at the first page
    navigate(paths.transactionsByRiskBand(next)); // keep the URL shareable/bookmarkable
  }

  function openTransaction(transaction: TransactionResponse): void {
    const runId = transaction.investigation?.runId ?? transaction.latestRunId;
    if (runId) navigate(paths.investigation(runId));
    else if (canStartInvestigation) void investigate(transaction.transactionId);
  }

  async function investigate(transactionId: string): Promise<void> {
    if (actionInFlight.current || investigatingId !== null || !canStartInvestigation) return;
    actionInFlight.current = true;
    setInvestigatingId(transactionId);
    const modelOverride = canChooseModel ? override : undefined;
    if (
      !attempt.current ||
      attempt.current.transactionId !== transactionId ||
      attempt.current.modelOverride !== modelOverride
    ) {
      attempt.current = { transactionId, modelOverride, key: crypto.randomUUID() };
    }
    try {
      const result = await client.startInvestigation(
        { transactionId, modelOverride },
        attempt.current.key,
      );
      attempt.current = null;
      navigate(paths.investigation(result.runId));
    } catch (caught) {
      const existingRunId =
        caught instanceof ApiError && caught.code === "investigation_in_progress"
          ? caught.details?.find((detail) => detail.field === "runId")?.message
          : undefined;
      if (existingRunId) navigate(paths.investigation(existingRunId));
      else notifyError(caught);
      if (caught instanceof ApiError && caught.status < 500) attempt.current = null;
      setInvestigatingId(null);
    } finally {
      actionInFlight.current = false;
    }
  }

  async function importCsv(file: File): Promise<void> {
    try {
      const result = await client.uploadCsv(file);
      setUploadReport(result);
      notify({
        tone: "positive",
        title:
          result.accepted > 0
            ? "Import accepted — screening queued"
            : "Import processed — no new transactions",
        description: `${result.accepted} added · ${result.duplicates} duplicate · ${result.rejected} rejected`,
      });
      setCursorStack([undefined]);
      state.reload();
      screening.refresh();
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

  async function downloadTemplate(): Promise<void> {
    setDownloading(true);
    try {
      const template = await client.getCsvTemplate();
      const url = URL.createObjectURL(new Blob([template.content], { type: "text/csv" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "fraudlens-synthetic.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      notifyError(caught);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Transactions"
        description="CSV imports are screened automatically. Open saved results or explicitly re-run an investigation."
        actions={canIngestTransactions ? <ImportButton onFileChange={onFileChange} /> : undefined}
      />
      <p className="text-body-sm text-body">
        Use synthetic demo data only. Upload the demo CSV unchanged for verified synthetic
        provenance and AI eligibility. Other files stay local; validation does not prove anonymity.
      </p>
      {canIngestTransactions ? (
        <div>
          <Button
            variant="tertiary"
            size="sm"
            disabled={downloading}
            onClick={() => void downloadTemplate()}
          >
            {downloading ? "Downloading…" : "Download synthetic demo CSV"}
          </Button>
        </div>
      ) : null}
      {uploadReport ? <CsvValidationReport report={uploadReport} /> : null}
      <ScreeningStatus
        progress={screening.progress}
        error={screening.error}
        onRetry={screening.refresh}
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
              openTransaction,
              canStartInvestigation ? investigate : undefined,
            );
            const rangeEnd = pageIndex * PAGE_SIZE + data.transactions.length;
            return (
              <>
                <ProvenanceStrip
                  total={data.total}
                  activeModelLabel={metricsState.data?.modelHealth.activeVersionLabel ?? null}
                />
                {canChooseModel ? (
                  <AsyncBoundary state={modelsState}>
                    {(models) =>
                      models ? (
                        <div className="max-w-sm">
                          <ModelSelector
                            versions={models.versions}
                            activeLabel={models.activeVersionLabel}
                            value={override}
                            onChange={setOverride}
                          />
                        </div>
                      ) : null
                    }
                  </AsyncBoundary>
                ) : null}
                <DataTable
                  caption="Transactions"
                  columns={columns}
                  rows={data.transactions}
                  rowKey={(transaction) => transaction.transactionId}
                  onRowClick={openTransaction}
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
        placeholder="Search reference, amount, or country"
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

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
