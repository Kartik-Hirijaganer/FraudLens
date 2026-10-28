/**
 * Summary: Authoritative case workspace combining risk evidence, preparation and independent review.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AlertDetail: compose backend-controlled preparation, frozen rounds and read-only outcomes.
 *
 * Notes:
 * - Every mutation reloads authoritative state; working narratives and submitted artifacts remain distinct.
 */
import { useCallback, useState } from "react";

import { AgentTimeline } from "../components/AgentTimeline";
import { DecisionRail } from "../components/DecisionRail";
import { SarStream } from "../components/SarStream";
import { RagPanel } from "../components/RagPanel";
import { Timeline } from "../components/Timeline";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Textarea } from "../components/ui/Textarea";
import { apiClient, statusLabel, type ApiClient, type SarDraftView } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { riskTone, type StatusTone } from "../lib/risk";
import { initialInvestigationState, type InvestigationState } from "../lib/investigation";
import { navigate, paths } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { useAsyncAction } from "../lib/useAsyncAction";
import { CasePreparation } from "./CasePreparation";
import { CaseChecking } from "./CaseChecking";
import { CaseHistory } from "./CaseHistory";
import { AlertRiskEvidence, ModelInputDisclosure } from "./AlertEvidence";

const SAR_STATUS_TONES: Record<string, StatusTone> = {
  approved: "positive",
  rejected: "negative",
  failed: "warning",
};

// The deterministic SAR quality gate is tri-state: `passed` is an assertion a gate actually ran
// and accepted the narrative, `failed` means it was rejected, and `not_run` covers human-edited
// and pre-0.5.0 narratives no evaluator ever saw.
const SAR_QUALITY_TONES: Record<SarDraftView["qualityStatus"], StatusTone> = {
  passed: "positive",
  failed: "negative",
  not_run: "warning",
};

interface AlertDetailProps {
  alertId: string;
  client?: ApiClient;
}

export function AlertDetail({ alertId, client = apiClient }: AlertDetailProps) {
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const detail = await client.getAlert(alertId);
    const investigation = await client.getInvestigation(detail.alert.runId);
    return { detail, investigation };
  }, [client, alertId]);
  const state = useAsync(load, [client, alertId]);
  const { busy, run } = useAsyncAction(state.reload);

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader title="Case workspace" description={`Alert ${alertId}`} />
      <AsyncBoundary state={state}>
        {({ detail, investigation }) => {
          const alertClosed = ["resolved", "dismissed"].includes(detail.alert.status);
          const timelineState: InvestigationState = {
            ...initialInvestigationState(),
            status: "completed",
            completedSteps: ["rules", "scoring", ...(detail.sarDraft ? ["sar"] : [])],
            sarStarted: detail.sarDraft !== null,
            sarText: detail.sarContent ?? detail.sarDraft?.content ?? "",
            sarDraftId: detail.sarDraft?.sarDraftId,
            sarStatus: detail.sarDraft?.status,
            sarModelId: detail.sarDraft?.modelId,
            alertId: detail.alert.alertId,
            workflowMode: detail.workflowMode,
            graphVersion: detail.graphVersion ?? undefined,
            revisionCount: detail.revisionCount,
            agentRuns: detail.agentExecutions,
            recorded:
              detail.sarDraft?.modelId === "mock" ||
              detail.agentExecutions.some((run) => run.modelId === "mock"),
          };
          return (
            <div className="gap-xl grid grid-cols-1 lg:grid-cols-3">
              <div className="gap-xl flex min-w-0 flex-col lg:col-span-2">
                <Card className="gap-lg flex flex-wrap items-center">
                  <Badge tone={riskTone(detail.alert.severity)}>
                    {humanize(detail.alert.severity)}
                  </Badge>
                  {detail.alert.origin === "seed" ? (
                    <Badge tone="neutral">Sample data</Badge>
                  ) : null}
                  <span className="text-body-md text-ink">{statusLabel(detail.alert.status)}</span>
                  {detail.alert.assignedToName ? (
                    <span className="text-body-sm text-body">
                      Assigned to{" "}
                      <span className="text-ink font-semibold">{detail.alert.assignedToName}</span>
                    </span>
                  ) : null}
                  {detail.alert.reviewFlags.map((flag) => (
                    <Badge key={flag.flag} tone="warning">
                      {flag.reason}
                    </Badge>
                  ))}
                </Card>

                <Card className="gap-lg flex flex-col">
                  <h2 className="text-display-xs text-ink">Risk evidence</h2>
                  <AlertRiskEvidence investigation={investigation} />
                </Card>

                <Card className="gap-lg flex flex-col">
                  <AgentTimeline state={timelineState} title="How this SAR was produced" />
                  <Button
                    variant="tertiary"
                    onClick={() => navigate(paths.investigation(detail.alert.runId))}
                  >
                    Open the investigation run
                  </Button>
                </Card>

                <Card className="gap-md flex flex-col">
                  <div className="gap-md flex items-center justify-between">
                    <h2 className="text-display-xs text-ink">SAR draft</h2>
                    {detail.sarDraft ? (
                      <div className="gap-sm flex flex-wrap">
                        <Badge tone={SAR_STATUS_TONES[detail.sarDraft.status] ?? "neutral"}>
                          {humanize(detail.sarDraft.status)}
                        </Badge>
                        <Badge tone={SAR_QUALITY_TONES[detail.sarDraft.qualityStatus]}>
                          Quality {humanize(detail.sarDraft.qualityStatus)}
                        </Badge>
                      </div>
                    ) : null}
                  </div>
                  {detail.sarDraft ? (
                    <>
                      <SarStream
                        text={detail.sarDraft.content}
                        streaming={false}
                        failed={detail.sarDraft.status === "failed"}
                        template={detail.sarDraft.modelId === "template"}
                      />
                      <RagPanel citations={detail.sarDraft.citations} />
                      {detail.sarDraft.modelId !== "template" ? (
                        <ModelInputDisclosure modelInput={detail.sarDraft.modelInput} />
                      ) : null}
                    </>
                  ) : (
                    <EmptyState
                      title="No SAR draft"
                      description="This alert's investigation did not produce a SAR draft."
                    />
                  )}
                </Card>

                <CaseHistory detail={detail} client={client} reload={state.reload} />
                <Card className="gap-md flex flex-col">
                  <h2 className="text-display-xs text-ink">Activity</h2>
                  {detail.actions.length === 0 ? (
                    <EmptyState title="No actions yet" />
                  ) : (
                    <Timeline
                      items={detail.actions.map((action) => ({
                        id: action.actionId,
                        title: humanize(action.action),
                        meta: `${
                          action.fromStatus && action.toStatus
                            ? `${statusLabel(action.fromStatus)} → ${statusLabel(action.toStatus)} · `
                            : ""
                        }${formatDateTime(action.createdAt)}`,
                        body: action.note,
                      }))}
                    />
                  )}
                </Card>
              </div>

              <DecisionRail
                title={
                  alertClosed
                    ? "Case closed"
                    : detail.alert.status === "escalated"
                      ? "Submitted — read only"
                      : "Prepare case"
                }
              >
                <Button variant="tertiary" onClick={state.reload}>
                  Reload case
                </Button>
                <p className="text-caption text-mute">Case version {detail.alert.caseVersion}</p>
                {alertClosed ? (
                  <p className="text-body-sm text-body">
                    {statusLabel(detail.alert.status)}. The outcome and reasons are preserved in
                    case history.
                  </p>
                ) : (
                  <>
                    <CasePreparation
                      key={detail.alert.caseVersion}
                      detail={detail}
                      client={client}
                      reload={state.reload}
                    />
                    <CaseChecking detail={detail} client={client} reload={state.reload} />
                    {detail.alert.capabilities.comment.allowed ? (
                      <div className="gap-md flex flex-col">
                        <Textarea
                          label="Case comment"
                          value={note}
                          rows={3}
                          maxLength={2000}
                          onChange={(event) => setNote(event.target.value)}
                        />
                        <Button
                          variant="secondary"
                          disabled={busy || !note.trim()}
                          onClick={() =>
                            void run(
                              () =>
                                client.actOnAlert(alertId, {
                                  action: "comment",
                                  note,
                                  expectedCaseVersion: detail.alert.caseVersion,
                                }),
                              "Comment added",
                            )
                          }
                        >
                          Add comment
                        </Button>
                        <p className="text-caption text-mute">
                          Comments and requests for changes do not make you a SAR preparer.
                        </p>
                      </div>
                    ) : (
                      <p className="text-body-sm text-mute">
                        {detail.alert.capabilities.comment.reason}
                      </p>
                    )}
                  </>
                )}
              </DecisionRail>
            </div>
          );
        }}
      </AsyncBoundary>
    </section>
  );
}
