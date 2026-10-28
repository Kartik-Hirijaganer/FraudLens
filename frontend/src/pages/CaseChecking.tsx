/**
 * Summary: Independent checker decision form against the active immutable submission.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - CaseChecking: approve, return or decline with required reason and compatible outcome.
 *
 * Notes:
 * - No inline narrative edit is offered in checking; server capabilities explain independence.
 */
import { useRef, useState } from "react";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Textarea } from "../components/ui/Textarea";
import type { AlertDetailResponse, ApiClient, DecisionRequest, TrainingLabel } from "../lib/api";
import { TRAINING_LABEL_OPTIONS } from "../lib/options";
import { useAsyncAction } from "../lib/useAsyncAction";

export function CaseChecking({
  detail,
  client,
  reload,
}: {
  detail: AlertDetailResponse;
  client: ApiClient;
  reload: () => void;
}) {
  const [decision, setDecision] = useState<DecisionRequest["decision"]>("approve");
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<TrainingLabel>("confirmed_fraud");
  const keys = useRef(new Map<string, string>());
  const { busy, run } = useAsyncAction(reload);
  const submission = detail.submissions.find((item) => item.decision === null);
  if (!submission || detail.alert.status !== "escalated") return null;
  const capability = detail.alert.capabilities[decision];
  const options = TRAINING_LABEL_OPTIONS.filter((option) =>
    decision === "approve"
      ? ["confirmed_fraud", "false_negative"].includes(option.value)
      : ["benign", "false_positive"].includes(option.value),
  );
  const selectedOutcome =
    options.find((option) => option.value === outcome)?.value ?? options[0].value;
  function decide(): Promise<unknown> {
    const payload: DecisionRequest = {
      decision,
      reason,
      expectedCaseVersion: detail.alert.caseVersion,
      ...(decision === "return" ? {} : { outcome: selectedOutcome }),
    };
    const fingerprint = JSON.stringify(payload);
    let key = keys.current.get(fingerprint);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(fingerprint, key);
    }
    return client.decideSubmission(detail.alert.alertId, submission!.submissionId, payload, key);
  }
  return (
    <div className="gap-md flex flex-col">
      <h2 className="text-display-xs text-ink">Independent review</h2>
      <p className="text-body-sm text-body">
        Review frozen submission {submission.round}. Its narrative and evidence cannot be edited
        during checking.
      </p>
      <Select
        label="Review decision"
        value={decision}
        onChange={(event) => setDecision(event.target.value as DecisionRequest["decision"])}
        options={[
          { value: "approve", label: "Approve SAR and close case" },
          { value: "return", label: "Return for changes" },
          { value: "decline", label: "Decline and close without SAR" },
        ]}
      />
      <Textarea
        label="Decision reason or required changes"
        value={reason}
        rows={3}
        maxLength={2000}
        onChange={(event) => setReason(event.target.value)}
      />
      {decision !== "return" ? (
        <Select
          label="Case outcome"
          value={selectedOutcome}
          onChange={(event) => setOutcome(event.target.value as TrainingLabel)}
          options={options}
        />
      ) : null}
      {!capability.allowed ? (
        <p className="text-body-sm text-warning-deep">{capability.reason}</p>
      ) : null}
      <Button
        disabled={busy || !capability.allowed || !reason.trim()}
        onClick={() =>
          void run(
            decide,
            decision === "return"
              ? "Returned to original maker"
              : "Decision recorded and case closed",
          )
        }
      >
        {decision === "approve"
          ? "Approve submission"
          : decision === "return"
            ? "Return submission"
            : "Decline submission"}
      </Button>
    </div>
  );
}
