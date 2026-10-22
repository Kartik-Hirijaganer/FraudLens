/**
 * Summary: Value-free upload outcomes with verified synthetic provenance and drafting mode.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - CsvValidationReport: render accepted, duplicate and rejected counts with CSV row numbers.
 *
 * Notes:
 * - The API returns bounded, safe reasons; no rejected cell, header or identifier is displayed.
 */
import type { CsvUploadResponse } from "../lib/api";
import { Card } from "./ui/Card";

export function CsvValidationReport({ report }: { report: CsvUploadResponse }) {
  return (
    <Card className="gap-md flex flex-col">
      <div role="status" className="gap-sm flex flex-col">
        <h2 className="text-display-xs text-ink">CSV validation report</h2>
        <p className="text-body-md text-body">
          {report.accepted} accepted · {report.duplicates} duplicate · {report.rejected} rejected
        </p>
        <p className="text-body-sm text-body">
          {report.trustedSynthetic
            ? report.draftingMode === "ai"
              ? "Verified synthetic demo CSV. Newly accepted flagged cases can use AI drafting after evidence and quality checks."
              : "Verified synthetic demo CSV. Providers are disabled; newly accepted flagged cases use a mock draft."
            : "Unverified CSV. Flagged cases use a Template-generated draft; external AI drafting stays disabled."}{" "}
          Accepted rows are queued for automatic screening. Human review and approval are required.
        </p>
      </div>
      {report.sampleErrors.length > 0 ? (
        <ul aria-label="Validation rejection samples" className="text-body-sm text-warning-content">
          {report.sampleErrors.map((error) => (
            <li key={error.index}>
              Row {error.rowNumber ?? error.index + 2}: {error.reasonCode ?? error.code} —{" "}
              {error.message}
            </li>
          ))}
        </ul>
      ) : null}
      {report.rejected > report.sampleErrors.length ? (
        <p className="text-caption text-body">Only a bounded sample of rejected rows is shown.</p>
      ) : null}
    </Card>
  );
}
