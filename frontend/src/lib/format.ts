/**
 * Summary: Small, pure display formatters shared across the analyst/admin UI so the
 * same value is rendered consistently everywhere (rule 5: no duplication). Amounts
 * arrive from the API as Decimal-serialized strings, probabilities/scores as 0..1
 * floats, and timestamps as ISO strings; these turn them into locale-aware,
 * human-readable text. Every function is pure (no app/DOM state) and total — bad or
 * missing input degrades to a safe placeholder rather than throwing in render.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - formatCurrency: render an amount + ISO-4217 currency as a localized money string.
 * - formatPercent: render a 0..1 fraction as a percentage (e.g. 0.873 -> "87.3%").
 * - formatDateTime: render an ISO timestamp as a short localized date-time.
 * - humanize: turn a snake_case / dotted code into Title Case words (e.g. "in_review").
 *
 * Notes:
 * - Formatters never throw: an unparseable amount/date falls back to a dash so a single
 *   malformed field can't blank the whole page.
 */
const PLACEHOLDER = "—";

export function formatCurrency(amount: string | number, currency: string): string {
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(value)) {
    return PLACEHOLDER;
  }
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value);
  } catch {
    // Unknown/invalid currency code — fall back to a plain number + raw code.
    return `${value.toLocaleString()} ${currency}`;
  }
}

export function formatPercent(fraction: number, digits = 1): string {
  if (!Number.isFinite(fraction)) {
    return PLACEHOLDER;
  }
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return PLACEHOLDER;
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function humanize(code: string): string {
  const words = code.replace(/[._]+/g, " ").trim();
  if (!words) {
    return PLACEHOLDER;
  }
  return words
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
