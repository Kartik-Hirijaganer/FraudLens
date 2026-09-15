/**
 * Summary: Display copy and pure status/evidence projections for the investigation wizard.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - statusPill: map run state to semantic copy.
 * - evidenceChips: derive only chips backed by arrived evidence.
 * - STEP_COPY:
 *
 * Notes:
 * - Brand color is not used as a status channel.
 */
import { humanize } from "../lib/format";
import type { InvestigationState, ShapFeature } from "../lib/investigation";
import { riskTone, type StatusTone } from "../lib/risk";

interface StatusPill {
  tone: StatusTone;
  label: string;
}

export function statusPill(state: InvestigationState): StatusPill {
  switch (state.status) {
    case "completed":
      return { tone: "positive", label: "Auto-run complete" };
    case "failed":
      return { tone: "negative", label: "Auto-run failed" };
    case "drafting-blocked":
      return { tone: "warning", label: "Drafting blocked" };
    case "retrying":
      return {
        tone: "warning",
        label: `Retrying · attempt ${Math.max(1, state.attempt)}/${state.maxAttempts}`,
      };
    case "running":
      return state.attempt > 0
        ? { tone: "neutral", label: `Running · attempt ${state.attempt}/${state.maxAttempts}` }
        : { tone: "neutral", label: "Auto-run in progress" };
    default:
      return { tone: "neutral", label: "Pending worker" };
  }
}

// The chip reports the signed SHAP contribution rather than the transformed raw feature value,
// because a top absolute driver may either increase or reduce risk.
function topDriverLabel(feature: ShapFeature): string {
  const name = humanize(feature.feature);
  if (!Number.isFinite(feature.shapValue)) {
    return `Top driver: ${name}`;
  }
  const direction = feature.shapValue >= 0 ? "risk driver" : "risk reducer";
  const contribution = `${feature.shapValue >= 0 ? "+" : ""}${feature.shapValue.toFixed(3)}`;
  return `Top ${direction}: ${name} · SHAP ${contribution}`;
}

// The data-driven evidence summary chips shown under the stepper. Each entry is emitted
// only when its evidence exists, so the row grows as the auto-run lands.
export function evidenceChips(
  state: InvestigationState,
  includeEnrichment: boolean,
): { tone: StatusTone; label: string }[] {
  const chips: { tone: StatusTone; label: string }[] = [];
  const riskValue = state.riskScore ?? state.fraudProbability;
  if (riskValue !== undefined) {
    const band = state.riskBand ? humanize(state.riskBand) : undefined;
    chips.push({
      tone: state.riskBand ? riskTone(state.riskBand) : "neutral",
      label: band ? `Risk: ${band} · ${riskValue.toFixed(2)}` : `Risk · ${riskValue.toFixed(2)}`,
    });
  }
  const topDriver = state.topFeatures[0];
  if (topDriver) {
    chips.push({ tone: "neutral", label: topDriverLabel(topDriver) });
  }
  const topHit = state.ruleHits[0];
  if (topHit && topHit.ruleType) {
    chips.push({ tone: "neutral", label: humanize(topHit.ruleType) });
  }
  const count = state.citations.length;
  if (includeEnrichment && count > 0) {
    chips.push({ tone: "neutral", label: `${count} regulatory citation${count === 1 ? "" : "s"}` });
  }
  return chips;
}

export const STEP_COPY: Record<string, { heading: string; subtitle: string }> = {
  risk: {
    heading: "Confirm the risk assessment",
    subtitle: "The auto-run scored this transaction. Confirm the risk before moving on.",
  },
  drivers: {
    heading: "Review the model drivers",
    subtitle: "These features moved the score the most — confirm they make sense.",
  },
  citations: {
    heading: "Check the regulatory citations",
    subtitle: "The regulations the draft will rely on — confirm they're on point.",
  },
  sar: {
    heading: "Draft the SAR narrative",
    subtitle: "Generated from the evidence above. Read every line before approving it.",
  },
  submit: {
    heading: "Approve the SAR",
    subtitle: "Record the internal review decision. Regulatory submission is a separate process.",
  },
  outcome: {
    heading: "Review the outcome",
    subtitle: "The score stayed below the alert threshold, so enrichment stopped after analysis.",
  },
};
