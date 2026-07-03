/**
 * Summary: Compact risk indicator for tables. It renders risk as a colored dot
 * using the centralized `riskTone` vocabulary, with an optional visible label and
 * an always-present accessible label.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - RiskDot: render a semantic risk dot for a band/severity.
 *
 * Notes:
 * - Wise green is not used as a risk tone; this follows `lib/risk.ts`.
 */
import { humanize } from "../lib/format";
import { riskTone, toneDotClass } from "../lib/risk";
import { cx } from "../lib/cx";

interface RiskDotProps {
  band: string | null;
  showLabel?: boolean;
}

export function RiskDot({ band, showLabel = false }: RiskDotProps) {
  const label = band ? humanize(band) : "Unscored";
  return (
    <span className="gap-sm inline-flex items-center">
      <span
        aria-hidden="true"
        className={cx("h-sm w-sm rounded-full", toneDotClass(riskTone(band ?? "")))}
      />
      <span className={showLabel ? "text-body-sm text-ink" : "sr-only"}>{label}</span>
    </span>
  );
}
