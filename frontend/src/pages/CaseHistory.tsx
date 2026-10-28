/**
 * Summary: Frozen submission rounds, actors, reasons, version history and approved PDF recovery.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - CaseHistory: render immutable round details and download only an authorized artifact.
 *
 * Notes:
 * - PDF readiness is distinct from approval; pending and failed artifacts can be retried.
 */
import { RagPanel } from "../components/RagPanel";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import type { AlertDetailResponse, ApiClient } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { useAsyncAction } from "../lib/useAsyncAction";

export function CaseHistory({
  detail,
  client,
  reload,
}: {
  detail: AlertDetailResponse;
  client: ApiClient;
  reload: () => void;
}) {
  const { busy, run } = useAsyncAction(reload);
  async function download(submissionId: string): Promise<void> {
    const blob = await client.downloadSubmissionPdf(detail.alert.alertId, submissionId);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `sar-${submissionId}.pdf`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return (
    <>
      {detail.submissions.map((item) => (
        <Card key={item.submissionId} className="gap-md flex flex-col">
          <div className="gap-sm flex flex-wrap items-center">
            <h2 className="text-display-xs text-ink">Submission {item.round}</h2>
            <Badge
              tone={
                item.decision === "approve"
                  ? "positive"
                  : item.decision === "decline"
                    ? "negative"
                    : "neutral"
              }
            >
              {item.decision ? humanize(item.decision) : "Awaiting independent review"}
            </Badge>
          </div>
          <p className="text-body-sm text-body">
            {formatDateTime(item.createdAt)} · {humanize(item.recommendation)} · SAR version{" "}
            {item.sarDraft?.version ?? "None"}
          </p>
          <dl className="gap-sm text-body-sm flex flex-col break-words">
            <div>
              <dt className="font-semibold">Maker account / person</dt>
              <dd>
                {item.makerAccountId} / {item.makerPersonId}
              </dd>
            </div>
            <div>
              <dt className="font-semibold">Submitted by account</dt>
              <dd>{item.submittedByAccountId ?? item.makerAccountId}</dd>
            </div>
            <div>
              <dt className="font-semibold">Submission rationale</dt>
              <dd className="whitespace-pre-wrap">{item.rationale}</dd>
            </div>
            {item.decision ? (
              <>
                <div>
                  <dt className="font-semibold">Decision reason</dt>
                  <dd className="whitespace-pre-wrap">{item.reason}</dd>
                </div>
                <div>
                  <dt className="font-semibold">Outcome</dt>
                  <dd>{humanize(item.outcome ?? "Returned for changes")}</dd>
                </div>
                <div>
                  <dt className="font-semibold">Checker account / person</dt>
                  <dd>
                    {item.checkerAccountId} / {item.checkerPersonId}
                  </dd>
                </div>
              </>
            ) : null}
          </dl>
          {item.sarDraft ? (
            <details open={item.decision === null}>
              <summary className="text-body-sm text-ink cursor-pointer font-semibold">
                Frozen submitted narrative
              </summary>
              <p className="text-body-sm text-body py-md whitespace-pre-wrap break-words">
                {item.sarDraft.content}
              </p>
              <RagPanel citations={item.sarDraft.citations} />
            </details>
          ) : (
            <p className="text-body-sm text-body">
              No SAR submitted. Approval is unavailable; a checker may return or decline.
            </p>
          )}
          <details>
            <summary className="text-body-sm text-ink cursor-pointer font-semibold">
              Frozen evidence and participants
            </summary>
            <pre className="bg-canvas-soft p-md text-caption overflow-auto whitespace-pre-wrap break-words rounded-md">
              {JSON.stringify(
                { ...item.evidence, preparerPersonIds: item.contributorPersonIds },
                null,
                2,
              )}
            </pre>
          </details>
          {item.decision === "approve" ? (
            <div className="gap-sm flex flex-wrap items-center" aria-live="polite">
              <span className="text-body-sm text-body">PDF {item.pdfStatus}</span>
              <Button
                variant="secondary"
                disabled={busy || item.pdfStatus !== "ready"}
                onClick={() => void run(() => download(item.submissionId))}
              >
                Download PDF
              </Button>
              {item.canRetryPdf && item.pdfStatus !== "ready" ? (
                <Button
                  variant="tertiary"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => client.retrySubmissionPdf(detail.alert.alertId, item.submissionId),
                      "PDF recovery queued",
                    )
                  }
                >
                  Retry PDF
                </Button>
              ) : null}
            </div>
          ) : null}
        </Card>
      ))}
      <Card className="gap-md flex flex-col">
        <h2 className="text-display-xs text-ink">Draft versions</h2>
        {detail.draftVersions.map((draft) => (
          <details key={draft.sarDraftId}>
            <summary className="text-body-sm text-ink cursor-pointer">
              Version {draft.version} · {humanize(draft.status)} · {formatDateTime(draft.createdAt)}
            </summary>
            <p className="text-body-sm text-body py-md whitespace-pre-wrap break-words">
              {draft.content || "Drafting failed — artifact retained."}
            </p>
          </details>
        ))}
      </Card>
    </>
  );
}
