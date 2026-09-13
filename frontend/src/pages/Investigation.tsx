/**
 * Summary: The "Build the case" investigation page (plan §5.4, §10.2, §16 Phase 11,
 * redesigned). It opens the SSE stream for a run, folds the server-sent events into
 * `InvestigationState`, and presents alerted runs as a guided five-step wizard — Risk →
 * Drivers → Citations → SAR draft → Approval. No-alert runs use a compact Risk → Drivers →
 * Outcome path and never imply that a SAR exists.
 * confirming each before moving on. The auto-run populates the evidence in the background
 * (a status pill reads starting / in progress / complete / failed); a step's "continue"
 * CTA stays disabled until that step's evidence has streamed in (`caseStepReady`). The
 * stream replays persisted events from the start (revisiting a finished run reconstructs
 * it) and is closed on the terminal event; a mid-run connection error falls back to the
 * authoritative snapshot.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Investigation: render the streamed evidence as the build-the-case wizard.
 *
 * Notes:
 * - The SSE factory is injectable so tests drive the stream deterministically.
 * - Every rendered value (chips, gauge, drivers, citations, SAR narrative) is derived from
 *   the streamed state — nothing is hardcoded — so seeded/demo runs render end to end.
 * - The brand green marks only the active step + its primary CTA (the current action); all
 *   status colour comes from the semantic palette (DESIGN.md).
 * - "Regenerate" on the SAR step POSTs to the regenerate endpoint (a new persisted draft
 *   version) while the button shows a spinner and the draft dims/pulses; on success the new
 *   narrative replaces the shown text, on failure an error toast fires and the draft is kept.
 * - "Approve SAR" requires a raised alert + approvable draft, POSTs an approval through
 *   the existing SAR-review endpoint, and navigates only after the persisted review succeeds.
 */
import { useEffect, useState } from "react";

import { AgentTimeline } from "../components/AgentTimeline";
import { CaseStepper } from "../components/CaseStepper";
import { DecisionRail } from "../components/DecisionRail";
import { ErrorState } from "../components/feedback/ErrorState";
import { Spinner } from "../components/feedback/Spinner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { apiClient, type ApiClient } from "../lib/api";
import { formatInvestigationRef, humanize } from "../lib/format";
import {
  CASE_STEPS,
  INVESTIGATION_EVENTS,
  NO_ALERT_CASE_STEPS,
  caseStepReady,
  initialInvestigationState,
  investigationStateFromSnapshot,
  reduceInvestigation,
  type InvestigationState,
} from "../lib/investigation";
import { navigate, paths } from "../lib/router";
import { hasPermission, useSession } from "../lib/session";
import { createSseClient, type SseHandle } from "../lib/sse";
import { notify, notifyError } from "../lib/toast";
import { InvestigationStepBody } from "./InvestigationStepBody";
import { STEP_COPY, evidenceChips, statusPill } from "./investigationView";

const TERMINAL_EVENTS = new Set(["run.completed", "run.failed"]);
const APPROVABLE_SAR_STATUSES = new Set(["draft", "reviewed"]);

interface InvestigationProps {
  runId: string;
  client?: ApiClient;
  createStream?: typeof createSseClient;
}

export function Investigation({
  runId,
  client = apiClient,
  createStream = createSseClient,
}: InvestigationProps) {
  const [state, setState] = useState<InvestigationState>(initialInvestigationState);
  const [connectionError, setConnectionError] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [regenerating, setRegenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const session = useSession();
  const canStartInvestigation = hasPermission(session, "startInvestigation");
  const canReviewSar = hasPermission(session, "reviewSar");

  useEffect(() => {
    setState(initialInvestigationState());
    setConnectionError(false);
    setCurrentStep(0);
    setRegenerating(false);
    setSubmitting(false);
    let closed = false;
    let terminalReached = false;
    let handle: SseHandle | null = null;
    handle = createStream({
      url: client.investigationStreamUrl(runId),
      events: INVESTIGATION_EVENTS,
      onMessage: (message) => {
        setState((prev) => reduceInvestigation(prev, message));
        if (TERMINAL_EVENTS.has(message.type)) {
          terminalReached = true;
          handle?.close();
          void client.getInvestigation(runId).then(
            (snapshot) => {
              if (!closed) {
                setState((current) => investigationStateFromSnapshot(snapshot, current));
              }
            },
            () => undefined,
          );
        }
      },
      onError: () => {
        if (terminalReached || closed) {
          return;
        }
        setConnectionError(true);
        void client.getInvestigation(runId).then(
          (snapshot) => {
            if (!closed) {
              setState((current) => investigationStateFromSnapshot(snapshot, current));
            }
          },
          () => undefined,
        );
      },
    });
    return () => {
      closed = true;
      handle?.close();
    };
  }, [client, runId, createStream]);

  const terminal = state.status === "completed" || state.status === "failed";
  const showColdStart = state.status === "starting" && state.completedSteps.length === 0;
  const streaming = state.sarStarted && state.status === "running";
  // The gauge shows whichever value has landed, and is captioned to match it. `riskScore` is the
  // BLENDED policy score on the band scale; `fraudProbability` is the model's calibrated
  // probability. With a rare-event model the two differ by orders of magnitude, so a single
  // "fraud risk" caption misreported one of them as the other.
  const gaugeValue = state.riskScore ?? state.fraudProbability;
  const gaugeLabel = state.riskScore !== undefined ? "risk score" : "fraud probability";
  const pill = statusPill(state.status);
  const isNoAlertOutcome = state.status === "completed" && state.alertId === undefined;
  const chips = evidenceChips(state, !isNoAlertOutcome);
  const caseSteps = isNoAlertOutcome ? NO_ALERT_CASE_STEPS : CASE_STEPS;
  const activeStep = Math.min(currentStep, caseSteps.length - 1);
  const stepKey = caseSteps[activeStep].key;
  const copy = STEP_COPY[stepKey];
  const isLastStep = activeStep === caseSteps.length - 1;
  const nextStep = isLastStep ? undefined : caseSteps[activeStep + 1];
  const canAdvance = caseStepReady(state, isLastStep ? "submit" : (nextStep?.key ?? "submit"));
  const hasApprovableDraft =
    state.sarDraftId !== undefined &&
    state.sarStatus !== undefined &&
    APPROVABLE_SAR_STATUSES.has(state.sarStatus);
  const canSubmit =
    canAdvance && state.alertId !== undefined && hasApprovableDraft && canReviewSar && !submitting;
  const canRunPrimary = isLastStep ? (isNoAlertOutcome ? canAdvance : canSubmit) : canAdvance;
  const primaryLabel = isLastStep
    ? isNoAlertOutcome
      ? "Back to transactions"
      : "Approve SAR"
    : `Looks good — continue to ${nextStep?.label.toLowerCase()} →`;

  async function handlePrimary(): Promise<void> {
    if (!canRunPrimary) {
      return;
    }
    if (isLastStep) {
      if (isNoAlertOutcome) {
        navigate(paths.transactions);
        return;
      }
      if (state.alertId === undefined) {
        return;
      }
      setSubmitting(true);
      try {
        await client.reviewSar(state.alertId, { decision: "approve" });
        notify({
          tone: "positive",
          title: "SAR approved",
          description: "The internal approval was recorded and PDF generation was queued.",
        });
        navigate(paths.alerts);
      } catch (caught) {
        notifyError(caught);
      } finally {
        setSubmitting(false);
      }
      return;
    }
    setCurrentStep(Math.min(activeStep + 1, caseSteps.length - 1));
  }

  function handleBack(): void {
    setCurrentStep(Math.max(activeStep - 1, 0));
  }

  async function handleRegenerate(): Promise<void> {
    if (regenerating) {
      return;
    }
    setRegenerating(true);
    notify({
      tone: "neutral",
      title: "Regenerating the SAR draft",
      description: "Rebuilding the narrative from the confirmed evidence…",
    });
    try {
      const draft = await client.regenerateSar(runId);
      setState((prev) => ({ ...prev, sarText: draft.content, sarDraftId: draft.sarDraftId }));
      notify({ tone: "positive", title: "SAR draft regenerated" });
    } catch (caught) {
      notifyError(caught);
    } finally {
      setRegenerating(false);
    }
  }

  return (
    <section className="gap-xl flex flex-col">
      <header className="gap-lg flex flex-col lg:flex-row lg:items-start lg:justify-between">
        <div className="gap-sm flex flex-col">
          <nav aria-label="Breadcrumb" className="gap-xs text-body-sm flex items-center">
            <span className="text-mute font-semibold">{formatInvestigationRef(runId)}</span>
            <span aria-hidden="true" className="text-mute">
              /
            </span>
            <span className="text-ink font-semibold">Investigation</span>
          </nav>
          <h1 className="text-display-md text-ink">
            {isNoAlertOutcome ? "Review the analysis" : "Build the case"}
          </h1>
          <p className="text-body-lg text-body">
            {isNoAlertOutcome
              ? "The run completed below the alert threshold. Review the score and model drivers."
              : "FraudLens walks the evidence one step at a time. You confirm each before moving on."}
          </p>
        </div>
        <Badge tone={pill.tone} className="shrink-0">
          {pill.label}
        </Badge>
      </header>

      {connectionError && !terminal ? (
        <Card>
          <p className="text-body-sm text-warning-deep">
            Live updates were interrupted; showing the latest saved state. The investigation
            continues in the background.
          </p>
        </Card>
      ) : null}

      {state.status === "failed" ? (
        <ErrorState
          title="Investigation failed"
          description={
            state.errorCode ? humanize(state.errorCode) : "The investigation could not complete."
          }
        />
      ) : null}

      <div className="gap-xl grid grid-cols-1 lg:grid-cols-3">
        <Card className="gap-xl flex flex-col lg:col-span-2">
          <CaseStepper steps={caseSteps} currentStep={activeStep} />

          {chips.length > 0 ? (
            <div className="gap-sm flex flex-wrap">
              {chips.map((chip) => (
                <Badge key={chip.label} tone={chip.tone}>
                  {chip.label}
                </Badge>
              ))}
            </div>
          ) : null}

          <div className="border-canvas-soft border-t" />

          <div className="gap-lg flex flex-col">
            <div className="gap-xs flex flex-col">
              <h2 className="text-display-xs text-ink">
                Step {activeStep + 1} · {copy.heading}
              </h2>
              <p className="text-body-md text-mute">{copy.subtitle}</p>
            </div>

            <InvestigationStepBody
              stepKey={stepKey}
              state={state}
              showColdStart={showColdStart}
              gaugeValue={gaugeValue}
              gaugeLabel={gaugeLabel}
              streaming={streaming}
              regenerating={regenerating}
              canAdvance={canAdvance}
              hasApprovableDraft={hasApprovableDraft}
              canReviewSar={canReviewSar}
            />

            <div className="gap-md flex flex-col">
              <div className="gap-md flex flex-col sm:flex-row">
                {stepKey === "sar" && canStartInvestigation ? (
                  <Button
                    variant="secondary"
                    onClick={() => void handleRegenerate()}
                    disabled={regenerating}
                  >
                    {regenerating ? (
                      <span className="gap-sm inline-flex items-center">
                        <Spinner label="Regenerating the SAR draft" />
                        Regenerating…
                      </span>
                    ) : (
                      "Regenerate"
                    )}
                  </Button>
                ) : null}
                <Button
                  variant="primary"
                  className="grow"
                  disabled={!canRunPrimary || regenerating}
                  onClick={() => void handlePrimary()}
                >
                  {submitting ? (
                    <span className="gap-sm inline-flex items-center">
                      <Spinner label="Approving the SAR" />
                      Approving…
                    </span>
                  ) : (
                    primaryLabel
                  )}
                </Button>
              </div>
              {!canAdvance ? (
                <p className="text-body-sm text-mute">The auto-run hasn't reached this step yet.</p>
              ) : null}
            </div>
          </div>

          <div className="border-canvas-soft border-t" />

          <div className="gap-md text-body-sm flex items-center justify-between">
            {activeStep > 0 ? (
              <button type="button" onClick={handleBack} className="text-ink font-semibold">
                ← Back to {caseSteps[activeStep - 1].label.toLowerCase()}
              </button>
            ) : (
              <span />
            )}
            <span className="text-mute">
              Step {activeStep + 1} of {caseSteps.length}
            </span>
          </div>
        </Card>
        <DecisionRail title="">
          <AgentTimeline state={state} title="Machine progress" />
        </DecisionRail>
      </div>
    </section>
  );
}
