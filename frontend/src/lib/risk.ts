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
 *
 * Notes:
 * - `StatusTone` is the single tone vocabulary consumed by Badge and the gauge, so a new
 *   tone is added in exactly one place.
 * - An unknown/empty value degrades to the neutral tone (never throws in render).
 */
export type StatusTone = "positive" | "warning" | "negative" | "neutral";

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
