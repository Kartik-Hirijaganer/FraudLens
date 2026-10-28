/**
 * Summary: Explicit deterministic pattern findings shared by investigation and alert review.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - RuleFindings: render matched patterns, reasons and semantic severity badges.
 *
 * Notes:
 * - Findings remain available independently of SAR drafting and model explanations.
 */
import { humanize } from "../lib/format";
import type { InvestigationRuleHit } from "../lib/investigationTypes";
import { riskTone } from "../lib/risk";
import { Badge } from "./ui/Badge";

export function RuleFindings({ hits }: { hits: InvestigationRuleHit[] }) {
  return (
    <section aria-label="Rule findings" className="gap-md flex min-w-0 grow flex-col">
      <h3 className="text-body-md text-ink font-semibold">Rule findings</h3>
      {hits.length === 0 ? (
        <p className="text-body-sm text-body">No deterministic rule patterns matched.</p>
      ) : (
        <ul className="gap-sm flex w-full flex-col">
          {hits.map((hit) => (
            <li
              key={hit.code}
              className="gap-md bg-canvas-soft p-lg flex items-start justify-between rounded-lg"
            >
              <div className="gap-xxs flex min-w-0 flex-col">
                <span className="text-body-md text-ink font-semibold">
                  {humanize(hit.ruleType)}
                </span>
                <span className="text-body-sm text-body">{hit.reason}</span>
              </div>
              <Badge tone={riskTone(hit.severity)}>{humanize(hit.severity)}</Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
