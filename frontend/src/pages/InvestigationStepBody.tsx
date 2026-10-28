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
import { riskTone } from "../lib/risk";
import type { ApiClient, SarDraftView } from "../lib/api";
import { InvestigationSar } from "./InvestigationSar";
import type { InvestigationState } from "../lib/investigation";
import { paths } from "../lib/router";
import { isSarDecided } from "./investigationView";

interface InvestigationStepBodyProps {
  stepKey: string;
  state: InvestigationState;
  showColdStart: boolean;
  gaugeValue: number | undefined;
  gaugeLabel: string;
  streaming: boolean;
  client: ApiClient;
  sarDirty: boolean;
  onSarDirtyChange: (dirty: boolean) => void;
  onSarSaved: (draft: SarDraftView) => void;
}

export function InvestigationStepBody({
  stepKey,
  state,
  showColdStart,
  gaugeValue,
  gaugeLabel,
  streaming,
  client,
  sarDirty,
  onSarDirtyChange,
  onSarSaved,
}: InvestigationStepBodyProps) {
  const decisionNotice = isSarDecided(state) ? (
    <div className="gap-md bg-canvas-soft p-lg flex flex-col rounded-lg">
      <p className="text-body-md text-ink font-semibold">SAR {state.sarStatus}</p>
      <p className="text-body-sm text-body">
        An authorized reviewer has already {state.sarStatus} this SAR. The recorded decision is
        final. Approval and regeneration are unavailable for this report.
      </p>
      {state.alertId ? (
        <a
          className="text-body-sm text-ink font-semibold underline"
          href={paths.alertDetail(state.alertId)}
        >
          View alert
        </a>
      ) : null}
    </div>
  ) : null;

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
        <div className="gap-md flex flex-col">
          {decisionNotice}
          {state.alertId && !streaming ? (
            <InvestigationSar
              alertId={state.alertId}
              state={state}
              client={client}
              dirty={sarDirty}
              onDirtyChange={onSarDirtyChange}
              onSaved={onSarSaved}
            />
          ) : (
            <SarStream
              text={state.sarText}
              streaming={streaming}
              failed={state.status === "failed"}
              template={state.sarModelId === "template"}
            />
          )}
        </div>
      );
    case "submit":
      if (decisionNotice) {
        return decisionNotice;
      }
      return (
        <div className="gap-md bg-canvas-soft p-lg flex flex-col rounded-lg">
          <p className="text-body-md text-ink font-semibold">Continue in the case workspace</p>
          <p className="text-body-sm text-body">
            Take ownership, prepare a working SAR or no-SAR recommendation, and submit it for an
            independent person to review. Submitted narratives are locked.
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
