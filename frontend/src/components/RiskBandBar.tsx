/**
 * Summary: The dashboard's transaction risk-band mix (plan Phase 3b). `GET /dashboard/metrics`
 * already returns `transactions.byRiskBand` — including the `unscored` bucket — and the Dashboard
 * already fetches it, so this renders that ALREADY-LOADED field as five labelled chips (low,
 * medium, high, critical, unscored) with their counts. Nothing is derived, inferred, or faked: an
 * absent band reads `0`, because `byRiskBand` is an open map on the API. Each scored band links
 * to the Transactions page pre-filtered by that band, which is the demo's path from "the mix" to
 * "the rows".
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - RiskBandBar: render the five band chips with counts and their band-filter deep links.
 *
 * Notes:
 * - Bands are coloured through `RiskDot`/`riskTone`, so the semantic positive/warning/negative
 *   palette is shared with the tables and badges (rule 5). Wise green is never a status colour.
 * - `unscored` is deliberately NOT a link: it is not a `RiskBand` value, and `GET /transactions`
 *   can only filter by a real band ("unscored" = `risk_band IS NULL`, which the query contract
 *   cannot express). Rather than invent a client-side filter the API does not support, the chip
 *   reports the count only — the whole point of those rows is that a visitor investigates them.
 * - `RiskDot` renders "Unscored" for a null band, so the fifth chip reuses the same component
 *   instead of restating that label.
 */
import { RiskDot } from "./RiskDot";
import type { TransactionMetrics } from "../lib/api";
import { paths } from "../lib/router";

// The scored bands, in escalating order — the same order the API and filters use.
const SCORED_BANDS = ["low", "medium", "high", "critical"] as const;
// The API's bucket key for rows that have never been scored (`risk_band IS NULL`).
const UNSCORED_KEY = "unscored";

const CHIP_CLASS =
  "gap-sm rounded-pill bg-canvas-soft px-lg py-sm flex items-center whitespace-nowrap";

export function RiskBandBar({ metrics }: { metrics: TransactionMetrics }) {
  const count = (key: string): number => metrics.byRiskBand[key] ?? 0;
  return (
    <section
      aria-labelledby="risk-band-bar-label"
      className="gap-md bg-canvas p-xl flex flex-col rounded-xl"
    >
      <p
        id="risk-band-bar-label"
        className="text-caption text-mute font-semibold uppercase tracking-wide"
      >
        Transactions by risk band
      </p>
      <ul className="gap-sm flex flex-wrap">
        {SCORED_BANDS.map((band) => (
          <li key={band}>
            <a
              href={paths.transactionsByRiskBand(band)}
              className={`${CHIP_CLASS} hover:bg-primary-neutral transition-colors`}
            >
              <RiskDot band={band} showLabel />
              <span className="text-body-sm text-ink font-semibold">{count(band)}</span>
            </a>
          </li>
        ))}
        <li>
          <span className={CHIP_CLASS}>
            <RiskDot band={null} showLabel />
            <span className="text-body-sm text-ink font-semibold">{count(UNSCORED_KEY)}</span>
          </span>
        </li>
      </ul>
    </section>
  );
}
