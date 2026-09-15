/**
 * Summary: Evidence body for each step of the build-the-case investigation wizard.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - InvestigationStepBody: render the active risk, drivers, citations, SAR, or outcome body.
 *
 * Notes:
 * - Every rendered value comes from accumulated investigation state.
 */
import { FraudGauge } from "../components/FraudGauge";
import { RagPanel } from "../components/RagPanel";
import { SarStream } from "../components/SarStream";
import { ShapBarChart } from "../components/ShapBarChart";
import { ColdStartProgress } from "../components/feedback/ColdStartProgress";
import { EmptyState } from "../components/feedback/EmptyState";
import { Badge } from "../components/ui/Badge";
import { humanize } from "../lib/format";
import type { InvestigationState } from "../lib/investigation";
import { riskTone } from "../lib/risk";

interface InvestigationStepBodyProps {
  stepKey: string;
  state: InvestigationState;
  showColdStart: boolean;
  gaugeValue: number | undefined;
  gaugeLabel: string;
  streaming: boolean;
  regenerating: boolean;
  canAdvance: boolean;
  hasApprovableDraft: boolean;
  canReviewSar: boolean;
}

export function InvestigationStepBody({
  stepKey,
  state,
  showColdStart,
  gaugeValue,
  gaugeLabel,
  streaming,
  regenerating,
  canAdvance,
  hasApprovableDraft,
  canReviewSar,
}: InvestigationStepBodyProps) {
  switch (stepKey) {
    case "risk":
      if (showColdStart) {
        return <ColdStartProgress />;
      }
      if (gaugeValue === undefined) {
        return (
          <EmptyState
            title="No risk score yet"
            description="The gauge fills in once the auto-run finishes scoring."
          />
        );
      }
      return (
        <div className="gap-xl flex flex-col items-center lg:flex-row lg:items-start">
          <FraudGauge value={gaugeValue} band={state.riskBand ?? ""} label={gaugeLabel} />
          {state.ruleHits.length > 0 ? (
            <ul className="gap-sm flex w-full grow flex-col">
              {state.ruleHits.map((hit) => (
                <li
                  key={hit.code}
                  className="gap-md bg-canvas-soft p-lg flex items-start justify-between rounded-lg"
                >
                  <div className="gap-xxs flex flex-col">
                    <span className="text-body-md text-ink font-semibold">
                      {humanize(hit.ruleType)}
                    </span>
                    <span className="text-body-sm text-body">{hit.reason}</span>
                  </div>
                  <Badge tone={riskTone(hit.severity)}>{humanize(hit.severity)}</Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      );
    case "drivers":
      return <ShapBarChart features={state.topFeatures} />;
    case "citations":
      return <RagPanel citations={state.citations} mode={state.ragMode} />;
    case "sar":
      return (
        <SarStream
          text={state.sarText}
          streaming={streaming}
          failed={state.status === "failed"}
          regenerating={regenerating}
        />
      );
    case "submit":
      return (
        <div className="gap-md bg-canvas-soft p-lg flex flex-col rounded-lg">
          <p className="text-body-md text-ink font-semibold">Ready for internal approval</p>
          <p className="text-body-sm text-body">
            {canAdvance
              ? !hasApprovableDraft
                ? "This investigation has no draft that is eligible for approval."
                : !canReviewSar
                  ? "Reviewer permission is required to approve this report."
                  : "You've confirmed the risk, drivers, citations, and narrative. Approval is recorded internally; it does not submit the SAR to FinCEN."
              : "The auto-run is still finishing. You can approve once every step has completed."}
          </p>
        </div>
      );
    case "outcome":
      return (
        <div className="gap-md bg-canvas-soft p-lg flex flex-col rounded-lg">
          <p className="text-body-md text-ink font-semibold">Analysis complete — no alert</p>
          <p className="text-body-sm text-body">
            The blended score did not cross the alert threshold. FraudLens recorded the score and
            model drivers, then stopped before regulatory retrieval and SAR drafting.
          </p>
        </div>
      );
    default:
      return null;
  }
}
