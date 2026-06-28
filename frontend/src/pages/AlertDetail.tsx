/**
 * Summary: The alert review page (plan §5.4, §10.4, §16 Phase 11; FR-8). It shows the
 * alert summary + force-review flags, the SAR draft (narrative + grounded citations +
 * status) with the human review actions (approve / reject-with-reason / edit), the triage
 * actions (comment / escalate / resolve-with-label / dismiss — resolve writes a training
 * label), and the append-only activity history. Every action routes through one helper
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

import { DecisionRail } from "../components/DecisionRail";
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
import { apiClient, statusLabel, type ApiClient, type TrainingLabel } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { TRAINING_LABEL_OPTIONS } from "../lib/options";
import { riskTone, type StatusTone } from "../lib/risk";
import { useAsync } from "../lib/useAsync";
import { useAsyncAction } from "../lib/useAsyncAction";

const SAR_STATUS_TONES: Record<string, StatusTone> = {
  approved: "positive",
  rejected: "negative",
  failed: "warning",
};

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

  return (
    <section className="gap-xl flex flex-col">
      <PageHeader title="Alert review" description={`Alert ${alertId}`} />
      <AsyncBoundary state={state}>
        {(detail) => (
          <div className="gap-xl grid grid-cols-1 lg:grid-cols-[1fr_320px]">
            <div className="gap-xl flex flex-col">
              <Card className="gap-lg flex flex-wrap items-center">
                <Badge tone={riskTone(detail.alert.severity)}>
                  {humanize(detail.alert.severity)}
                </Badge>
                <span className="text-body-md text-ink">{statusLabel(detail.alert.status)}</span>
                {detail.alert.reviewFlags.map((flag) => (
                  <Badge key={flag.flag} tone="warning">
                    {flag.reason}
                  </Badge>
                ))}
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
                    <pre className="bg-canvas-soft p-lg text-body-sm text-ink whitespace-pre-wrap break-words rounded-lg font-sans">
                      {detail.sarDraft.content}
                    </pre>
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

            <DecisionRail title="Actions">
              {detail.sarDraft ? (
                <div className="gap-md flex flex-col">
                  <h3 className="text-body-md text-ink font-semibold">SAR review</h3>
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () => client.reviewSar(alertId, { decision: "approve" }),
                        "SAR approved",
                      )
                    }
                  >
                    Approve
                  </Button>
                  <Textarea
                    label="Rejection reason"
                    value={reason}
                    rows={2}
                    onChange={(event) => setReason(event.target.value)}
                  />
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () => client.reviewSar(alertId, { decision: "reject", reason }),
                        "SAR rejected",
                      )
                    }
                  >
                    Reject
                  </Button>
                  <Textarea
                    label="Edit narrative"
                    value={editedContent}
                    rows={4}
                    onChange={(event) => setEditedContent(event.target.value)}
                  />
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () => client.reviewSar(alertId, { decision: "edit", editedContent }),
                        "SAR updated",
                      )
                    }
                  >
                    Save edit
                  </Button>
                </div>
              ) : null}

              <div className="gap-md flex flex-col">
                <h3 className="text-body-md text-ink font-semibold">Triage</h3>
                <Textarea
                  label="Note (optional)"
                  value={note}
                  rows={2}
                  onChange={(event) => setNote(event.target.value)}
                />
                <div className="gap-sm flex flex-col">
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () =>
                          client.actOnAlert(alertId, {
                            action: "comment",
                            note: note || undefined,
                          }),
                        "Comment added",
                      )
                    }
                  >
                    Comment
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
                    Escalate
                  </Button>
                  <Button
                    variant="tertiary"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () =>
                          client.actOnAlert(alertId, {
                            action: "dismiss",
                            note: note || undefined,
                          }),
                        "Alert dismissed",
                      )
                    }
                  >
                    Dismiss
                  </Button>
                </div>
                <Select
                  label="Resolution label"
                  options={TRAINING_LABEL_OPTIONS}
                  value={label}
                  onChange={(event) => setLabel(event.target.value as TrainingLabel)}
                />
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () =>
                        client.actOnAlert(alertId, {
                          action: "resolve",
                          label,
                          note: note || undefined,
                        }),
                      "Alert resolved",
                    )
                  }
                >
                  Resolve
                </Button>
              </div>
            </DecisionRail>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}
