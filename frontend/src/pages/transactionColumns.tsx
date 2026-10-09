/**
 * Summary: Transaction table projection with independent read and investigation actions.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - transactionColumns: render saved-result links and permission-gated mutation buttons.
 *
 * Notes:
 * - Nested actions stop propagation and never invoke the row handler.
 */
import { RiskDot } from "../components/RiskDot";
import type { Column } from "../components/ui/DataTable";
import type { TransactionResponse } from "../lib/api";
import {
  formatCurrency,
  formatDateTime,
  formatMaskedAccount,
  formatTransactionRef,
} from "../lib/format";

export function transactionColumns(
  investigatingId: string | null,
  open: (transaction: TransactionResponse) => void,
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

  return [
    ...columns,
    {
      id: "action",
      header: "Actions",
      srOnlyHeader: true,
      align: "right",
      cell: (transaction) => {
        const busy = investigatingId === transaction.transactionId;
        const displayRef = formatTransactionRef(transaction.transactionId, transaction.occurredAt);
        const run = transaction.investigation;
        const saved = Boolean(run || transaction.latestRunId);
        const active = Boolean(run && ["pending", "running", "retrying"].includes(run.status));
        const label = active
          ? "View progress"
          : run?.status === "failed"
            ? "View failed run"
            : "View results";
        return (
          <div className="gap-md flex flex-wrap items-center justify-end">
            {saved ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  open(transaction);
                }}
                className="text-body-sm text-ink font-semibold"
                aria-label={`${label} for transaction ${displayRef}`}
              >
                {label}
              </button>
            ) : null}
            {investigate ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  void investigate(transaction.transactionId);
                }}
                disabled={investigatingId !== null || active}
                aria-label={`${saved ? "Re-run" : "Investigate"} transaction ${displayRef}`}
                className="text-body-sm text-ink font-semibold disabled:opacity-50"
              >
                {busy ? "Starting…" : saved ? "Re-run" : "Investigate"}
              </button>
            ) : null}
            {active ? (
              <span className="text-caption text-body">
                {run?.status === "pending"
                  ? "Queued"
                  : run?.status === "retrying"
                    ? "Retrying"
                    : "Screening"}
              </span>
            ) : null}
          </div>
        );
      },
    },
  ];
}
