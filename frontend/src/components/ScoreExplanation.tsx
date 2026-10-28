/**
 * Summary: Distinguish a policy risk score, calibrated probability and explicit rule findings.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ScoreExplanation: explain persisted scoring evidence without implying a fraud diagnosis.
 *
 * Notes:
 * - The blend weight is runtime configuration, so this copy does not hardcode a percentage.
 */
import { formatPercent } from "../lib/format";

interface ScoreExplanationProps {
  probability: number | null | undefined;
}

export function ScoreExplanation({ probability }: ScoreExplanationProps) {
  return (
    <div className="gap-xs text-body-sm text-body flex max-w-prose flex-col">
      <p>The policy risk score determines risk bands and alerts. It is not a fraud probability.</p>
      {probability !== null && probability !== undefined ? (
        <p>Calibrated model probability: {formatPercent(probability)}</p>
      ) : null}
      <p>Rule findings identify patterns for investigation. A match does not establish fraud.</p>
    </div>
  );
}
