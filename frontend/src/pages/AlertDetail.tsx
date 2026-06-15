/**
 * Summary: The alert review page (plan §5.4, §10.4, §16 Phase 11; FR-8). It shows the
 * alert summary + force-review flags, the SAR draft (narrative + grounded citations +
 * status) with the human review actions (approve / reject-with-reason / edit), the triage
 * actions (comment / escalate / resolve-with-label / dismiss — resolve writes a training
 * label), and the append-only activity history. Every action routes through one helper
 * that toggles a busy guard, toasts the outcome, and reloads the authoritative detail.
 *
 * Key classes:
 * - AlertDetailProps: props (the alertId + an injectable ApiClient for tests).
 *
 * Key functions:
 * - AlertDetail: render the SAR review + triage workflow for one alert.
 *
 * Notes:
 * - Errors surface as PHI-free toasts via `notifyError`; the note/reason/edit fields are
 *   length-bounded server-side and masked before persistence (§5.4).
 */
import { useCallback, useState } from "react";

import { RagPanel } from "../components/RagPanel";
import { AsyncBoundary } from "../components/feedback/AsyncBoundary";
import { EmptyState } from "../components/feedback/EmptyState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Select";
import { Textarea } from "../components/ui/Textarea";
import { apiClient, type ApiClient, type TrainingLabel } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { riskTone, type StatusTone } from "../lib/risk";
import { useAsync } from "../lib/useAsync";
import { useAsyncAction } from "../lib/useAsyncAction";

const LABEL_OPTIONS = [
  { value: "confirmed_fraud", label: "Confirmed fraud" },
  { value: "false_positive", label: "False positive" },
  { value: "false_negative", label: "False negative" },
  { value: "benign", label: "Benign" },
];

const SAR_STATUS_TONES: Record<string, StatusTone> = {
  approved: "positive",
  rejected: "negative",
  failed: "warning",
};

export interface AlertDetailProps {
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
      <header className="gap-sm bg-canvas-soft p-3xl flex flex-col rounded-xl">
        <h1 className="text-display-md text-ink">Alert review</h1>
        <p className="text-body-sm text-mute">Alert {alertId}</p>
      </header>
      <AsyncBoundary state={state}>
        {(detail) => (
          <div className="gap-xl flex flex-col">
            <Card className="gap-lg flex flex-wrap items-center">
              <Badge tone={riskTone(detail.alert.severity)}>
                {humanize(detail.alert.severity)}
              </Badge>
              <span className="text-body-md text-ink">{humanize(detail.alert.status)}</span>
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
                  <div className="gap-sm flex flex-wrap">
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
                  </div>
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
                </>
              ) : (
                <EmptyState
                  title="No SAR draft"
                  description="This alert's investigation did not produce a SAR draft."
                />
              )}
            </Card>

            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">Triage</h2>
              <Textarea
                label="Note (optional)"
                value={note}
                rows={2}
                onChange={(event) => setNote(event.target.value)}
              />
              <div className="gap-sm flex flex-wrap">
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () =>
                        client.actOnAlert(alertId, { action: "comment", note: note || undefined }),
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
                        client.actOnAlert(alertId, { action: "escalate", note: note || undefined }),
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
                        client.actOnAlert(alertId, { action: "dismiss", note: note || undefined }),
                      "Alert dismissed",
                    )
                  }
                >
                  Dismiss
                </Button>
              </div>
              <div className="gap-md flex flex-wrap items-end">
                <div className="grow">
                  <Select
                    label="Resolution label"
                    options={LABEL_OPTIONS}
                    value={label}
                    onChange={(event) => setLabel(event.target.value as TrainingLabel)}
                  />
                </div>
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
            </Card>

            <Card className="gap-md flex flex-col">
              <h2 className="text-display-xs text-ink">Activity</h2>
              {detail.actions.length === 0 ? (
                <EmptyState title="No actions yet" />
              ) : (
                <ul className="gap-sm flex flex-col">
                  {detail.actions.map((action) => (
                    <li
                      key={action.actionId}
                      className="gap-xxs border-canvas-soft pt-sm flex flex-col border-t first:border-0 first:pt-0"
                    >
                      <span className="text-body-sm text-ink font-semibold">
                        {humanize(action.action)}
                      </span>
                      <span className="text-caption text-mute">
                        {action.fromStatus && action.toStatus
                          ? `${humanize(action.fromStatus)} → ${humanize(action.toStatus)} · `
                          : ""}
                        {formatDateTime(action.createdAt)}
                      </span>
                      {action.note ? (
                        <span className="text-body-sm text-body">{action.note}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}
