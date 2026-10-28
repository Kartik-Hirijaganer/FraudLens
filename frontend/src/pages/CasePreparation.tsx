/**
 * Summary: Maker preparation, manual SAR recovery, versioned saves and governed submission.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - CasePreparation: render backend-controlled preparation actions and blocking explanations.
 *
 * Notes:
 * - The command key survives retries of an identical payload; person identity is never sent.
 */
import { useEffect, useRef, useState } from "react";
import { SarStream } from "../components/SarStream";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Textarea } from "../components/ui/Textarea";
import type { AlertDetailResponse, ApiClient, SarDraftView, SubmissionRequest } from "../lib/api";
import { useAsyncAction } from "../lib/useAsyncAction";

export function CasePreparation({
  detail,
  client,
  reload,
  narrativeOnly = false,
  onDirtyChange,
  onDraftSaved,
}: {
  detail: AlertDetailResponse;
  client: ApiClient;
  reload: () => void;
  narrativeOnly?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  onDraftSaved?: (draft: SarDraftView) => void;
}) {
  const { alert, sarDraft } = detail;
  const caps = alert.capabilities;
  const [content, setContent] = useState(sarDraft?.content ?? "");
  const [rationale, setRationale] = useState("");
  const [recommendation, setRecommendation] = useState<"sar" | "no_sar">("sar");
  const [editing, setEditing] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const keys = useRef(new Map<string, string>());
  const { busy, run } = useAsyncAction(reload);
  const dirty = content !== (sarDraft?.content ?? "");
  const usable = Boolean(sarDraft?.content.trim()) && sarDraft?.status === "draft";
  useEffect(() => {
    if (narrativeOnly && editing) editorRef.current?.querySelector("textarea")?.focus();
  }, [narrativeOnly, editing]);

  function changeContent(value: string): void {
    setContent(value);
    onDirtyChange?.(value !== (sarDraft?.content ?? ""));
  }

  async function saveNarrative(): Promise<void> {
    const saved = await client.saveSar(alert.alertId, {
      content,
      expectedCaseVersion: alert.caseVersion,
      expectedSarVersion: sarDraft?.version ?? null,
    });
    setContent(saved.content);
    setEditing(false);
    onDraftSaved?.(saved);
    onDirtyChange?.(false);
  }

  function submit(): Promise<unknown> {
    const payload: SubmissionRequest = {
      expectedCaseVersion: alert.caseVersion,
      rationale,
      recommendation,
      ...(recommendation === "sar" && sarDraft ? { sarVersion: sarDraft.version } : {}),
    };
    const fingerprint = JSON.stringify(payload);
    let key = keys.current.get(fingerprint);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(fingerprint, key);
    }
    return client.submitCase(alert.alertId, payload, key);
  }
  return (
    <div ref={editorRef} className="gap-lg flex flex-col">
      {!narrativeOnly ? <h2 className="text-display-xs text-ink">Preparation</h2> : null}
      {narrativeOnly && (!editing || !caps.edit.allowed) ? (
        <SarStream
          text={sarDraft?.content ?? ""}
          streaming={false}
          failed={sarDraft?.status === "failed"}
          template={sarDraft?.modelId === "template"}
        />
      ) : null}
      {caps.pickup.allowed ? (
        <Button
          disabled={busy}
          onClick={() =>
            void run(
              () =>
                client.actOnAlert(alert.alertId, {
                  action: "assign",
                  expectedCaseVersion: alert.caseVersion,
                }),
              "Case taken",
            )
          }
        >
          {narrativeOnly ? "Take case to edit" : "Take case"}
        </Button>
      ) : null}
      {!caps.edit.allowed ? (
        <p className="text-body-sm text-body">{caps.edit.reason}</p>
      ) : narrativeOnly && !editing ? (
        <div className="gap-sm flex flex-col items-start">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit SAR
          </Button>
          <p className="text-body-sm text-body">
            Edit this working narrative here, then save your changes.
          </p>
        </div>
      ) : (
        <>
          <Textarea
            label="Working SAR narrative"
            value={content}
            disabled={busy}
            rows={10}
            maxLength={20000}
            onChange={(event) => changeContent(event.target.value)}
          />
          <div className="gap-sm flex flex-wrap">
            <Button
              variant="secondary"
              disabled={busy || !dirty || !content.trim()}
              onClick={() => void run(saveNarrative, "Working version saved")}
            >
              {narrativeOnly ? "Save changes" : "Save narrative"}
            </Button>
            {narrativeOnly ? (
              <Button
                variant="tertiary"
                disabled={busy}
                onClick={() => {
                  changeContent(sarDraft?.content ?? "");
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
            ) : null}
            {!narrativeOnly ? (
              <Button
                variant="secondary"
                disabled={busy || !caps.regenerate.allowed || dirty}
                onClick={() =>
                  void run(
                    () => client.regenerateSar(alert.runId, alert.caseVersion),
                    "SAR regenerated",
                  )
                }
              >
                Regenerate
              </Button>
            ) : null}
          </div>
          {!narrativeOnly ? (
            <>
              {!caps.regenerate.allowed ? (
                <p className="text-body-sm text-mute">{caps.regenerate.reason}</p>
              ) : null}
              <Select
                label="Recommendation"
                value={recommendation}
                onChange={(event) => setRecommendation(event.target.value as "sar" | "no_sar")}
                options={[
                  { value: "sar", label: "Submit SAR for review" },
                  { value: "no_sar", label: "Recommend no SAR" },
                ]}
              />
              <Textarea
                label="Submission or dismissal rationale"
                value={rationale}
                rows={3}
                maxLength={2000}
                onChange={(event) => setRationale(event.target.value)}
              />
              {dirty ? (
                <p className="text-body-sm text-warning-deep">
                  Save the narrative before submitting or regenerating.
                </p>
              ) : null}
              {recommendation === "sar" && !usable ? (
                <p className="text-body-sm text-body">
                  Author and save a usable SAR, or submit a no-SAR recommendation.
                </p>
              ) : null}
              <Button
                disabled={
                  busy ||
                  !caps.submit.allowed ||
                  !rationale.trim() ||
                  dirty ||
                  (recommendation === "sar" && !usable)
                }
                onClick={() => void run(submit, "Submitted — working draft locked")}
              >
                Send for review
              </Button>
              {!caps.submit.allowed ? (
                <p className="text-body-sm text-mute">{caps.submit.reason}</p>
              ) : null}
              <Button
                variant="secondary"
                disabled={busy || !caps.dismiss.allowed || !rationale.trim() || dirty}
                onClick={() =>
                  void run(
                    () =>
                      client.actOnAlert(alert.alertId, {
                        action: "dismiss",
                        expectedCaseVersion: alert.caseVersion,
                        note: rationale,
                      }),
                    "Case dismissed",
                  )
                }
              >
                Dismiss case
              </Button>
              {!caps.dismiss.allowed ? (
                <p className="text-body-sm text-mute">{caps.dismiss.reason}</p>
              ) : null}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
