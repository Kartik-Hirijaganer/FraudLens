/**
 * Summary: The "Build the case" investigation page (plan §5.4, §10.2, §16 Phase 11,
 * redesigned). It opens the SSE stream for a run, folds the server-sent events into
 * `InvestigationState`, and presents the evidence as a guided five-step wizard — Risk →
 * Drivers → Citations → SAR draft → Submit — that the analyst walks one step at a time,
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
 */
import { useEffect, useState } from "react";

import { CaseStepper } from "../components/CaseStepper";
import { FraudGauge } from "../components/FraudGauge";
import { RagPanel } from "../components/RagPanel";
import { SarStream } from "../components/SarStream";
import { ShapBarChart } from "../components/ShapBarChart";
import { ColdStartProgress } from "../components/feedback/ColdStartProgress";
import { EmptyState } from "../components/feedback/EmptyState";
import { ErrorState } from "../components/feedback/ErrorState";
import { Spinner } from "../components/feedback/Spinner";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { apiClient, type ApiClient, type InvestigationSnapshot } from "../lib/api";
import { formatAlertRef, humanize } from "../lib/format";
import {
  CASE_STEPS,
  INVESTIGATION_EVENTS,
  caseStepReady,
  initialInvestigationState,
  reduceInvestigation,
  type InvestigationState,
  type ShapFeature,
} from "../lib/investigation";
import { riskTone, type StatusTone } from "../lib/risk";
import { navigate, paths } from "../lib/router";
import { createSseClient, type SseHandle } from "../lib/sse";
import { notify, notifyError } from "../lib/toast";

const TERMINAL_EVENTS = new Set(["run.completed", "run.failed"]);

function snapshotToState(snapshot: InvestigationSnapshot): InvestigationState {
  const completedSteps: string[] = [];
  if (snapshot.ruleHits.length > 0) {
    completedSteps.push("rules");
  }
  if (snapshot.fraudProbability !== null) {
    completedSteps.push("scoring");
  }
  if (snapshot.topFeatures.length > 0) {
    completedSteps.push("shap");
  }
  if (snapshot.citations.length > 0) {
    completedSteps.push("rag");
  }
  if (snapshot.sarDraftId !== null) {
    completedSteps.push("sar");
  }
  const status: InvestigationState["status"] =
    snapshot.status === "completed"
      ? "completed"
      : snapshot.status === "failed"
        ? "failed"
        : "running";
  return {
    ...initialInvestigationState(),
    status,
    completedSteps,
    transactionId: snapshot.transactionId,
    ruleHits: snapshot.ruleHits,
    fraudProbability: snapshot.fraudProbability ?? undefined,
    modelVersion: snapshot.modelVersion ?? undefined,
    topFeatures: snapshot.topFeatures,
    citations: snapshot.citations,
    riskScore: snapshot.riskScore ?? undefined,
    riskBand: snapshot.riskBand ?? undefined,
    sarDraftId: snapshot.sarDraftId ?? undefined,
    errorCode: snapshot.errorCode ?? undefined,
  };
}

interface StatusPill {
  tone: StatusTone;
  label: string;
}

function statusPill(status: InvestigationState["status"]): StatusPill {
  switch (status) {
    case "completed":
      return { tone: "positive", label: "Auto-run complete" };
    case "failed":
      return { tone: "negative", label: "Auto-run failed" };
    case "running":
      return { tone: "neutral", label: "Auto-run in progress" };
    default:
      return { tone: "neutral", label: "Auto-run starting" };
  }
}

// The top SHAP driver, with its value appended (a "σ" suffix for z-score features), so the
// chip reads like "Top driver: Amount Zscore 4.1σ" — the driver AND how far it deviated.
function topDriverLabel(feature: ShapFeature): string {
  const name = humanize(feature.feature);
  if (!Number.isFinite(feature.value)) {
    return `Top driver: ${name}`;
  }
  const rounded = Math.round(feature.value * 10) / 10;
  const isZScore = /z[\s_-]?score/i.test(feature.feature);
  return `Top driver: ${name} ${rounded}${isZScore ? "σ" : ""}`;
}

// The data-driven evidence summary chips shown under the stepper. Each entry is emitted
// only when its evidence exists, so the row grows as the auto-run lands.
function evidenceChips(state: InvestigationState): { tone: StatusTone; label: string }[] {
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
  if (count > 0) {
    chips.push({ tone: "neutral", label: `${count} regulatory citation${count === 1 ? "" : "s"}` });
  }
  return chips;
}

const STEP_COPY: Record<string, { heading: string; subtitle: string }> = {
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
    subtitle: "Generated from the evidence above. Read every line — you own what you submit.",
  },
  submit: {
    heading: "Submit the report",
    subtitle: "One last look before you file. Submitting routes the case to the alerts queue.",
  },
};

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

  useEffect(() => {
    setState(initialInvestigationState());
    setConnectionError(false);
    setCurrentStep(0);
    setRegenerating(false);
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
              setState(snapshotToState(snapshot));
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
  const gaugeValue = state.riskScore ?? state.fraudProbability;
  const pill = statusPill(state.status);
  const chips = evidenceChips(state);

  const stepKey = CASE_STEPS[currentStep].key;
  const copy = STEP_COPY[stepKey];
  const isLastStep = currentStep === CASE_STEPS.length - 1;
  const nextStep = isLastStep ? undefined : CASE_STEPS[currentStep + 1];
  const canAdvance = caseStepReady(state, isLastStep ? "submit" : (nextStep?.key ?? "submit"));
  const primaryLabel = isLastStep
    ? "Submit the report"
    : `Looks good — continue to ${nextStep?.label.toLowerCase()} →`;

  function handlePrimary(): void {
    if (!canAdvance) {
      return;
    }
    if (isLastStep) {
      notify({
        tone: "positive",
        title: "SAR submitted for review",
        description: "The case has been routed to the alerts queue.",
      });
      navigate(paths.alerts);
      return;
    }
    setCurrentStep((step) => Math.min(step + 1, CASE_STEPS.length - 1));
  }

  function handleBack(): void {
    setCurrentStep((step) => Math.max(step - 1, 0));
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

  function renderStepBody() {
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
            <FraudGauge value={gaugeValue} band={state.riskBand ?? ""} />
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
            <p className="text-body-md text-ink font-semibold">Ready to file</p>
            <p className="text-body-sm text-body">
              {canAdvance
                ? "You've confirmed the risk, drivers, citations, and narrative. Submitting records your decision and routes the case to the alerts queue."
                : "The auto-run is still finishing. You can submit once every step has completed."}
            </p>
          </div>
        );
      default:
        return null;
    }
  }

  return (
    <section className="gap-xl flex flex-col">
      <header className="gap-lg flex flex-col lg:flex-row lg:items-start lg:justify-between">
        <div className="gap-sm flex flex-col">
          <nav aria-label="Breadcrumb" className="gap-xs text-body-sm flex items-center">
            <span className="text-mute font-semibold">{formatAlertRef(runId)}</span>
            <span aria-hidden="true" className="text-mute">
              /
            </span>
            <span className="text-ink font-semibold">Investigation</span>
          </nav>
          <h1 className="text-display-md text-ink">Build the case</h1>
          <p className="text-body-lg text-body">
            FraudLens walks the evidence one step at a time. You confirm each before moving on.
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

      <Card className="gap-xl flex flex-col">
        <CaseStepper steps={CASE_STEPS} currentStep={currentStep} />

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
              Step {currentStep + 1} · {copy.heading}
            </h2>
            <p className="text-body-md text-mute">{copy.subtitle}</p>
          </div>

          {renderStepBody()}

          <div className="gap-md flex flex-col">
            <div className="gap-md flex flex-col sm:flex-row">
              {stepKey === "sar" ? (
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
                disabled={!canAdvance || regenerating}
                onClick={handlePrimary}
              >
                {primaryLabel}
              </Button>
            </div>
            {!canAdvance ? (
              <p className="text-body-sm text-mute">The auto-run hasn't reached this step yet.</p>
            ) : null}
          </div>
        </div>

        <div className="border-canvas-soft border-t" />

        <div className="gap-md text-body-sm flex items-center justify-between">
          {currentStep > 0 ? (
            <button type="button" onClick={handleBack} className="text-ink font-semibold">
              ← Back to {CASE_STEPS[currentStep - 1].label.toLowerCase()}
            </button>
          ) : (
            <span />
          )}
          <span className="text-mute">
            Step {currentStep + 1} of {CASE_STEPS.length}
          </span>
        </div>
      </Card>
    </section>
  );
}
