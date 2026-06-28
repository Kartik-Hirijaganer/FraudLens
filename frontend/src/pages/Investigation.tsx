/**
 * Summary: The live investigation page (plan §5.4, §10.2, §16 Phase 11). It opens the SSE
 * stream for a run, folds the server-sent events into `InvestigationState`, and renders the
 * pipeline progress, the animated risk gauge, the SHAP drivers, the regulatory citations,
 * and the streaming SAR draft as each step lands. The stream replays persisted events from
 * the start (so revisiting a finished run reconstructs it) and is closed on the terminal
 * event to stop EventSource auto-reconnect; a mid-run connection error falls back to the
 * authoritative snapshot. A cold-start indicator shows until the first event arrives.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Investigation: render the streamed investigation view.
 *
 * Notes:
 * - The SSE factory is injectable so tests drive the stream deterministically; the gauge
 * shows the live probability before completion and the blended score+band after.
 */
import { useEffect, useState } from "react";

import { DecisionRail } from "../components/DecisionRail";
import { FraudGauge } from "../components/FraudGauge";
import { ProgressSteps } from "../components/ProgressSteps";
import { RagPanel } from "../components/RagPanel";
import { SarStream } from "../components/SarStream";
import { ShapBarChart } from "../components/ShapBarChart";
import { ColdStartProgress } from "../components/feedback/ColdStartProgress";
import { ErrorState } from "../components/feedback/ErrorState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient, type ApiClient, type InvestigationSnapshot } from "../lib/api";
import { humanize } from "../lib/format";
import {
  INVESTIGATION_EVENTS,
  initialInvestigationState,
  reduceInvestigation,
  type InvestigationState,
} from "../lib/investigation";
import { navigate, paths } from "../lib/router";
import { createSseClient, type SseHandle } from "../lib/sse";

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

  useEffect(() => {
    setState(initialInvestigationState());
    setConnectionError(false);
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
  const statusTone =
    state.status === "failed" ? "negative" : state.status === "completed" ? "positive" : "neutral";

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader
        title="Investigation"
        description={`Run ${runId}${state.modelVersion ? ` · model ${state.modelVersion}` : ""}`}
        aside={<Badge tone={statusTone}>{humanize(state.status)}</Badge>}
      />

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

      <div className="gap-xl grid grid-cols-1 lg:grid-cols-[1fr_320px]">
        <div className="gap-xl grid lg:grid-cols-[2fr_3fr]">
          <div className="gap-xl flex flex-col">
            <Card className="gap-lg flex flex-col">
              <h2 className="text-display-xs text-ink">Progress</h2>
              {showColdStart ? <ColdStartProgress /> : null}
              <ProgressSteps completedSteps={state.completedSteps} status={state.status} />
            </Card>
            {gaugeValue !== undefined ? (
              <Card className="gap-md flex flex-col items-center">
                <h2 className="text-display-xs text-ink">Risk</h2>
                <FraudGauge value={gaugeValue} band={state.riskBand ?? ""} />
              </Card>
            ) : null}
          </div>
          <div className="gap-xl flex flex-col">
            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">Top drivers</h2>
              <ShapBarChart features={state.topFeatures} />
            </Card>
            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">Regulatory citations</h2>
              <RagPanel citations={state.citations} mode={state.ragMode} />
            </Card>
            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">SAR draft</h2>
              <SarStream text={state.sarText} streaming={streaming} />
            </Card>
          </div>
        </div>
        <DecisionRail title="Decision">
          <div className="gap-xs flex flex-col">
            <span className="text-caption text-mute">Status</span>
            <span className="text-body-md text-ink">{humanize(state.status)}</span>
          </div>
          {state.riskBand ? (
            <div className="gap-xs flex flex-col">
              <span className="text-caption text-mute">Risk band</span>
              <span className="text-body-md text-ink">{humanize(state.riskBand)}</span>
            </div>
          ) : null}
          {state.status === "completed" ? (
            <Button variant="secondary" onClick={() => navigate(paths.alerts)}>
              View alerts
            </Button>
          ) : null}
        </DecisionRail>
      </div>
    </section>
  );
}
