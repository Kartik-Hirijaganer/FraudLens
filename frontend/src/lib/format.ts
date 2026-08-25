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
 * - formatDurationMs: render milliseconds as a compact human duration.
 * - formatAge: render an ISO timestamp as a compact relative age.
 * - formatAgo: render an ISO timestamp as a consistent "N{m,h,d} ago" phrase.
 * - formatAlertRef: render an alert id as a short human reference (e.g. "AL-4E18").
 * - formatInvestigationRef:
 * - formatTransactionRef: derive a stable production-style alias from a backend transaction id.
 * - formatMaskedAccount: reduce a masked account identifier to a compact last-four display.
 * - formatModelVersion: render registry labels as compact semantic-style versions.
 * - formatModelBuild: extract a short, traceable build reference from a registry label.
 * - formatMachineKey: turn rule/feature keys into analyst-friendly display labels.
 * - greeting: pick a time-of-day greeting ("Good morning/afternoon/evening").
 * - humanize: turn a snake_case / dotted code into Title Case words (e.g. "in_review").
 *
 * Notes:
 * - Formatters never throw: an unparseable amount/date falls back to a dash so a single
 * malformed field can't blank the whole page.
 */
const PLACEHOLDER = "—";

const MACHINE_KEY_LABELS: Record<string, string> = {
  amount_log: "Transaction amount (log scale)",
  hour_of_day: "Hour of day",
  day_of_week: "Day of week",
  is_round_amount: "Round-number amount",
  country_risk: "Country risk",
  channel_risk: "Payment-channel risk",
  velocity_24h: "Transaction volume (24 hours)",
  amount_24h_sum_log: "Total amount (24 hours, log scale)",
  distinct_countries_24h: "Distinct countries (24 hours)",
  is_outbound: "Outbound transaction",
  inbound_velocity_24h: "Inbound transaction volume (24 hours)",
  inbound_amount_24h_log: "Inbound amount (24 hours, log scale)",
  seconds_since_prev_txn_log: "Time since previous transaction (log scale)",
  distinct_channels_24h: "Distinct payment channels (24 hours)",
  round_amount_share_24h: "Round-number transaction share (24 hours)",
  dest_fan_in_24h: "Destination fan-in (24 hours)",
  dest_inbound_amount_24h_log: "Destination inbound amount (24 hours, log scale)",
  dest_outbound_velocity_24h: "Destination outbound transaction volume (24 hours)",
  dest_outbound_amount_24h_log: "Destination outbound amount (24 hours, log scale)",
};

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

export function formatDurationMs(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return PLACEHOLDER;
  }
  if (milliseconds < 1_000) {
    return `${Math.round(milliseconds)} ms`;
  }
  const seconds = milliseconds / 1_000;
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  }
  const wholeMinutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${wholeMinutes}m ${remainingSeconds}s`;
}

export function formatAge(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const elapsedMs = now.getTime() - date.getTime();
  if (Number.isNaN(date.getTime()) || Number.isNaN(elapsedMs)) {
    return PLACEHOLDER;
  }
  const elapsedSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) {
    return `${elapsedMinutes}m`;
  }
  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) {
    return `${elapsedHours}h`;
  }
  return `${Math.floor(elapsedHours / 24)}d ago`;
}

export function formatAgo(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const elapsedMs = now.getTime() - date.getTime();
  if (Number.isNaN(date.getTime()) || Number.isNaN(elapsedMs)) {
    return PLACEHOLDER;
  }
  const elapsedMinutes = Math.floor(Math.max(0, elapsedMs) / 60000);
  if (elapsedMinutes < 60) {
    return `${elapsedMinutes}m ago`;
  }
  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) {
    return `${elapsedHours}h ago`;
  }
  return `${Math.floor(elapsedHours / 24)}d ago`;
}

const _ALERT_REF_LENGTH = 4;

function formatCompactRef(value: string, prefix: string, knownPrefix: RegExp): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return PLACEHOLDER;
  }
  const tail = knownPrefix.exec(trimmed)?.[1] ?? trimmed;
  const alnum = tail.replace(/[^a-zA-Z0-9]/g, "") || tail;
  return `${prefix}-${alnum.slice(-_ALERT_REF_LENGTH).toUpperCase()}`;
}

export function formatAlertRef(alertId: string): string {
  return formatCompactRef(alertId, "AL", /^(?:alert|al)[-_](.+)$/i);
}

export function formatInvestigationRef(runId: string): string {
  return formatCompactRef(runId, "INV", /^(?:investigation|inv|run)[-_](.+)$/i);
}

export function formatTransactionRef(transactionId: string, occurredAt: string): string {
  const compactId = transactionId.replace(/[^a-zA-Z0-9]/g, "").toUpperCase();
  if (!compactId) {
    return PLACEHOLDER;
  }
  const date = new Date(occurredAt);
  const suffix = compactId.slice(-6);
  if (Number.isNaN(date.getTime())) {
    return `TXN-${suffix}`;
  }
  const datePart = [
    String(date.getUTCFullYear()).slice(-2),
    String(date.getUTCMonth() + 1).padStart(2, "0"),
    String(date.getUTCDate()).padStart(2, "0"),
  ].join("");
  return `TXN-${datePart}-${suffix}`;
}

export function formatMaskedAccount(account: string): string {
  const visibleTail = /([a-zA-Z0-9]{1,4})$/.exec(account.trim())?.[1];
  return visibleTail ? `•••• ${visibleTail.toUpperCase()}` : PLACEHOLDER;
}

export function formatModelVersion(label: string | null | undefined): string {
  if (!label) {
    return PLACEHOLDER;
  }
  const cleanLabel = label.trim().replace(/-fixture$/i, "");
  const explicitVersion = /(?:^|[-_])v(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:$|[-_])/i.exec(cleanLabel);
  if (explicitVersion) {
    return `v${explicitVersion[1]}.${explicitVersion[2] ?? "0"}.${explicitVersion[3] ?? "0"}`;
  }

  // Training bundles encode their feature-contract generation as "fsN". Use that stable
  // generation for the compact dashboard label while the build hash remains available below it.
  const featureSpecVersion = /(?:^|[-_])fs(\d+)(?:$|[-_])/i.exec(cleanLabel);
  if (featureSpecVersion) {
    return `v${featureSpecVersion[1]}.0.0`;
  }

  return cleanLabel || PLACEHOLDER;
}

export function formatModelBuild(label: string | null | undefined): string | null {
  if (!label) {
    return null;
  }
  const buildHash = /(?:^|[-_])([a-f\d]{7,})(?:$|[-_])/i.exec(label.trim());
  return buildHash ? `Build ${buildHash[1].slice(0, 8)}` : null;
}

export function formatMachineKey(key: string): string {
  const trimmed = key.trim();
  if (!trimmed) {
    return PLACEHOLDER;
  }
  const knownLabel = MACHINE_KEY_LABELS[trimmed.toLowerCase()];
  if (knownLabel) {
    return knownLabel;
  }
  const words = trimmed.replace(/[._]+/g, " ").replace(/\s+/g, " ").toLowerCase();
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) {
    return "Good morning";
  }
  if (hour < 18) {
    return "Good afternoon";
  }
  return "Good evening";
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
