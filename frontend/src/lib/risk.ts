/**
 * Summary: Maps the backend's shared risk vocabulary — risk bands and alert/drift
 * severities (low | medium | high | critical, see `Severity`/`RiskBand` on the API) —
 * onto the `wise` semantic status palette so bands and severities are coloured the
 * SAME way everywhere (rule 5: no duplication). Wise green is deliberately NOT a tone
 * here: it is the brand CTA accent, never a status colour (DESIGN.md Do/Don't), so the
 * "low/positive" tone uses the positive palette, not the brand green.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - riskTone: map a band/severity string onto a semantic StatusTone.
 * - severityRank: return a sortable severity rank for risk-first ordering.
 * - severityCounts: tally a list of severities into high/medium/low buckets.
 * - toneDotClass: map a StatusTone onto its indicator-dot background class.
 *
 * Notes:
 * - `StatusTone` is the single tone vocabulary consumed by Badge and the gauge, so a new
 *   tone is added in exactly one place.
 * - `toneDotClass` is the single source for tone dot colours (RiskDot + MetricCard) so the
 *   status-dot palette never diverges (rule 5: no duplication).
 * - An unknown/empty value degrades to the neutral tone (never throws in render).
 * - `severityCounts` folds `critical` into the `high` bucket (dashboard triage groups the
 *   two most-urgent bands together) and ignores anything else.
 */
export type StatusTone = "positive" | "warning" | "negative" | "neutral";

interface SeverityCounts {
  high: number;
  medium: number;
  low: number;
}

const BAND_TONES: Record<string, StatusTone> = {
  low: "positive",
  medium: "warning",
  high: "negative",
  critical: "negative",
};

const SEVERITY_RANKS: Record<string, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

export function riskTone(value: string): StatusTone {
  return BAND_TONES[value.toLowerCase()] ?? "neutral";
}

export function severityRank(value: string | null | undefined): number {
  return value ? (SEVERITY_RANKS[value.toLowerCase()] ?? 0) : 0;
}

const TONE_DOT_CLASS: Record<StatusTone, string> = {
  positive: "bg-positive",
  warning: "bg-warning",
  negative: "bg-negative",
  neutral: "bg-mute",
};

export function toneDotClass(tone: StatusTone): string {
  return TONE_DOT_CLASS[tone];
}

export function severityCounts(severities: readonly string[]): SeverityCounts {
  const counts: SeverityCounts = { high: 0, medium: 0, low: 0 };
  for (const value of severities) {
    switch (value.toLowerCase()) {
      case "critical":
      case "high":
        counts.high += 1;
        break;
      case "medium":
        counts.medium += 1;
        break;
      case "low":
        counts.low += 1;
        break;
    }
  }
  return counts;
}
