/**
 * Summary: The alert review page (plan §5.4, §10.4, §16 Phase 11; FR-8). It shows the
 * alert summary + force-review flags, the SAR draft (narrative + grounded citations +
 * status) with the human review actions (approve / reject-with-reason / edit), the triage
 * actions as a guided SAR-decision → case-outcome sequence, and the append-only activity
 * history. The shared machine timeline explains how the SAR was produced and links back to the
 * originating investigation run. Every action routes through one helper
 * that toggles a busy guard, toasts the outcome, and reloads the authoritative detail.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - AlertDetail: render the SAR review + triage workflow for one alert.
 *
 * Notes:
 * - Errors surface as PHI-free toasts via `notifyError`; the note/reason/edit fields are
 * length-bounded server-side and masked before persistence (§5.4).
 */
import { useCallback, useState } from "react";

import { AgentTimeline } from "../components/AgentTimeline";
import { DecisionRail } from "../components/DecisionRail";
import { Markdown } from "../components/Markdown";
import { RagPanel } from "../components/RagPanel";
import { Timeline } from "../components/Timeline";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { Textarea } from "../components/ui/Textarea";
import {
  apiClient,
  statusLabel,
  type AlertStatus,
  type ApiClient,
  type SarStatus,
  type TrainingLabel,
} from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { TRAINING_LABEL_OPTIONS } from "../lib/options";
import { riskTone, type StatusTone } from "../lib/risk";
import { initialInvestigationState, type InvestigationState } from "../lib/investigation";
import { navigate, paths } from "../lib/router";
import { hasPermission, useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";
import { useAsyncAction } from "../lib/useAsyncAction";

const SAR_STATUS_TONES: Record<string, StatusTone> = {
  approved: "positive",
  rejected: "negative",
  failed: "warning",
};

const APPROVED_OUTCOME_OPTIONS = TRAINING_LABEL_OPTIONS.filter(
  (option) => option.value === "confirmed_fraud" || option.value === "false_negative",
);
const REJECTED_OUTCOME_OPTIONS = TRAINING_LABEL_OPTIONS.filter(
  (option) => option.value === "false_positive" || option.value === "benign",
);
const OUTCOME_HELP: Record<TrainingLabel, string> = {
  confirmed_fraud: "The alert correctly identified suspicious or fraudulent activity.",
  false_negative: "Rules found suspicious activity that the model under-scored or missed.",
  false_positive: "The alert fired, but the reviewed activity was legitimate.",
  benign: "The activity was confirmed to be normal and non-suspicious.",
};

function isAlertClosed(status: AlertStatus): boolean {
  return status === "resolved" || status === "dismissed";
}

function isSarDecided(status: SarStatus | null): boolean {
  return status === "approved" || status === "rejected";
}

function outcomeOptions(status: SarStatus | null) {
  if (status === "approved") {
    return APPROVED_OUTCOME_OPTIONS;
  }
  if (status === "rejected") {
    return REJECTED_OUTCOME_OPTIONS;
  }
  return status === null ? TRAINING_LABEL_OPTIONS : [];
}

interface AlertDetailProps {
  alertId: string;
  client?: ApiClient;
}

export function AlertDetail({ alertId, client = apiClient }: AlertDetailProps) {
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [label, setLabel] = useState<TrainingLabel>("confirmed_fraud");

  const load = useCallback(() => client.getAlert(alertId), [client, alertId]);
  const state = useAsync(load, [client, alertId]);
  const { busy, run } = useAsyncAction(state.reload);
  const session = useSession();
  const canTriage = hasPermission(session, "triageAlert");
  const canReviewSar = hasPermission(session, "reviewSar");
  const canFinalize = hasPermission(session, "finalizeAlert");
  const hasAlertActions = canTriage || canFinalize;

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader title="Alert review" description={`Alert ${alertId}`} />
      <AsyncBoundary state={state}>
        {(detail) => {
          const sarStatus = detail.sarDraft?.status ?? null;
          const sarDecided = isSarDecided(sarStatus);
          const alertClosed = isAlertClosed(detail.alert.status);
          const availableOutcomes = outcomeOptions(sarStatus);
          const selectedOutcome = availableOutcomes.some((option) => option.value === label)
            ? label
            : availableOutcomes[0]?.value;
          const timelineState: InvestigationState = {
            ...initialInvestigationState(),
            status: "completed",
            completedSteps: ["rules", "scoring", ...(detail.sarDraft ? ["sar"] : [])],
            sarStarted: detail.sarDraft !== null,
            sarText: detail.sarContent ?? detail.sarDraft?.content ?? "",
            sarDraftId: detail.sarDraft?.sarDraftId,
            sarStatus: detail.sarDraft?.status,
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
            <div className="gap-xl grid grid-cols-1 lg:grid-cols-[1fr_320px]">
              <div className="gap-xl flex flex-col">
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
                      <Badge tone={SAR_STATUS_TONES[detail.sarDraft.status] ?? "neutral"}>
                        {humanize(detail.sarDraft.status)}
                      </Badge>
                    ) : null}
                  </div>
                  {detail.sarDraft ? (
                    <>
                      <div className="bg-canvas-soft p-lg rounded-lg">
                        <Markdown text={detail.sarDraft.content} />
                      </div>
                      <RagPanel citations={detail.sarDraft.citations} />
                    </>
                  ) : (
                    <EmptyState
                      title="No SAR draft"
                      description="This alert's investigation did not produce a SAR draft."
                    />
                  )}
                </Card>

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

              <DecisionRail title={alertClosed ? "Case closed" : "Review and close"}>
                {alertClosed ? (
                  <div className="gap-xs flex flex-col">
                    <p className="text-body-md text-ink font-semibold">
                      {statusLabel(detail.alert.status)}
                    </p>
                    <p className="text-body-sm text-mute">
                      The final decision is recorded in the activity history. No further changes are
                      allowed.
                    </p>
                  </div>
                ) : (
                  <>
                    {detail.sarDraft ? (
                      canReviewSar ? (
                        <div className="gap-md flex flex-col">
                          <h3 className="text-body-md text-ink font-semibold">1. SAR decision</h3>
                          {sarDecided ? (
                            <div className="gap-xs bg-canvas-soft p-md flex flex-col rounded-lg">
                              <p className="text-body-sm text-ink font-semibold">
                                SAR {humanize(sarStatus ?? "")}
                              </p>
                              <p className="text-body-sm text-body">
                                Choose the matching final case outcome in step 2.
                              </p>
                            </div>
                          ) : (
                            <>
                              <p className="text-body-sm text-body">
                                Approve the report when the activity is suspicious; reject it when a
                                SAR is not warranted.
                              </p>
                              <Button
                                disabled={busy || sarStatus === "failed"}
                                onClick={() =>
                                  void run(
                                    () => client.reviewSar(alertId, { decision: "approve" }),
                                    "SAR approved",
                                  )
                                }
                              >
                                Approve SAR
                              </Button>
                              <Textarea
                                label="Reason for rejection"
                                value={reason}
                                rows={2}
                                onChange={(event) => setReason(event.target.value)}
                              />
                              <Button
                                variant="secondary"
                                disabled={busy || !reason.trim()}
                                onClick={() =>
                                  void run(
                                    () =>
                                      client.reviewSar(alertId, {
                                        decision: "reject",
                                        reason,
                                      }),
                                    "SAR rejected",
                                  )
                                }
                              >
                                Reject SAR
                              </Button>
                              <Textarea
                                label="Edit narrative"
                                value={editedContent}
                                rows={4}
                                onChange={(event) => setEditedContent(event.target.value)}
                              />
                              <Button
                                variant="secondary"
                                disabled={busy || !editedContent.trim()}
                                onClick={() =>
                                  void run(
                                    () =>
                                      client.reviewSar(alertId, {
                                        decision: "edit",
                                        editedContent,
                                      }),
                                    "SAR updated",
                                  )
                                }
                              >
                                Save edit
                              </Button>
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="gap-xs flex flex-col">
                          <h3 className="text-body-md text-ink font-semibold">1. SAR decision</h3>
                          <p className="text-body-sm text-mute">
                            {sarDecided
                              ? `SAR ${humanize(sarStatus ?? "")}.`
                              : "Awaiting reviewer decision."}
                          </p>
                        </div>
                      )
                    ) : (
                      <div className="gap-xs flex flex-col">
                        <h3 className="text-body-md text-ink font-semibold">1. SAR decision</h3>
                        <p className="text-body-sm text-mute">
                          No SAR draft was produced. Continue with a manual case outcome.
                        </p>
                      </div>
                    )}

                    {hasAlertActions ? (
                      <>
                        <div className="gap-md flex flex-col">
                          <h3 className="text-body-md text-ink font-semibold">
                            Investigation updates
                          </h3>
                          <Textarea
                            label="Investigation note (optional)"
                            value={note}
                            rows={2}
                            onChange={(event) => setNote(event.target.value)}
                          />
                          {canTriage ? (
                            <div className="gap-sm flex flex-col">
                              <Button
                                variant="secondary"
                                disabled={busy || !note.trim()}
                                onClick={() =>
                                  void run(
                                    () =>
                                      client.actOnAlert(alertId, {
                                        action: "comment",
                                        note,
                                      }),
                                    "Note added",
                                  )
                                }
                              >
                                Add note
                              </Button>
                              <Button
                                variant="secondary"
                                disabled={busy}
                                onClick={() =>
                                  void run(
                                    () =>
                                      client.actOnAlert(alertId, {
                                        action: "escalate",
                                        note: note || undefined,
                                      }),
                                    "Alert escalated",
                                  )
                                }
                              >
                                {canReviewSar ? "Escalate for review" : "Send for review"}
                              </Button>
                            </div>
                          ) : null}
                        </div>

                        {canFinalize ? (
                          <div className="gap-md flex flex-col">
                            <h3 className="text-body-md text-ink font-semibold">2. Case outcome</h3>
                            {selectedOutcome ? (
                              <>
                                <Select
                                  label="Final outcome"
                                  options={availableOutcomes}
                                  value={selectedOutcome}
                                  onChange={(event) =>
                                    setLabel(event.target.value as TrainingLabel)
                                  }
                                />
                                <p className="text-body-sm text-mute">
                                  {OUTCOME_HELP[selectedOutcome]}
                                </p>
                                <Button
                                  disabled={busy}
                                  onClick={() =>
                                    void run(
                                      () =>
                                        client.actOnAlert(alertId, {
                                          action: "resolve",
                                          label: selectedOutcome,
                                          note: note || undefined,
                                        }),
                                      "Alert closed",
                                    )
                                  }
                                >
                                  Close alert
                                </Button>
                              </>
                            ) : (
                              <p className="bg-canvas-soft p-md text-body-sm text-body rounded-lg">
                                Complete step 1 by approving or rejecting the SAR draft.
                              </p>
                            )}
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <p className="text-body-sm text-mute">
                        Read-only access. Review the SAR, notes, and timeline without changing this
                        alert.
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
