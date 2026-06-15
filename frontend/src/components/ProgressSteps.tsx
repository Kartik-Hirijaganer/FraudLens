/**
 * Summary: The investigation pipeline progress tracker (plan §5.4, §16 Phase 11
 * ProgressSteps). It renders the five ordered steps (rules → scoring → SHAP →
 * regulations → SAR) and marks each done / active / failed / pending from the reduced
 * `InvestigationState`. The active step pulses while running (suppressed under reduced
 * motion) and carries `aria-current="step"` so assistive tech tracks progress.
 *
 * Key classes:
 * - ProgressStepsProps: props (completed step keys + the run status).
 *
 * Key functions:
 * - ProgressSteps: render the ordered step list with per-step state.
 *
 * Notes:
 * - The active/failed marker is whichever step comes next after the last completed one;
 *   colours use the semantic palette (never the brand green).
 */
import { cx } from "../lib/cx";
import { INVESTIGATION_STEPS, type InvestigationStatus } from "../lib/investigation";

export interface ProgressStepsProps {
  completedSteps: string[];
  status: InvestigationStatus;
}

const MARKER_CLASSES: Record<string, string> = {
  done: "bg-positive text-canvas",
  active: "bg-ink text-canvas motion-safe:animate-pulse",
  failed: "bg-negative text-canvas",
  pending: "bg-canvas-soft text-mute",
};

export function ProgressSteps({ completedSteps, status }: ProgressStepsProps) {
  const nextIndex = INVESTIGATION_STEPS.findIndex((step) => !completedSteps.includes(step.key));
  return (
    <ol className="gap-sm flex flex-col">
      {INVESTIGATION_STEPS.map((step, index) => {
        const done = completedSteps.includes(step.key);
        const isNext = !done && index === nextIndex;
        const active = isNext && status === "running";
        const failed = isNext && status === "failed";
        const state = done ? "done" : active ? "active" : failed ? "failed" : "pending";
        return (
          <li
            key={step.key}
            aria-current={active ? "step" : undefined}
            className="gap-md flex items-center"
          >
            <span
              aria-hidden="true"
              className={cx(
                "flex size-xl items-center justify-center rounded-full text-caption font-semibold",
                MARKER_CLASSES[state],
              )}
            >
              {state === "done" ? "✓" : state === "failed" ? "!" : index + 1}
            </span>
            <span className={cx("text-body-md", state === "pending" ? "text-mute" : "text-ink")}>
              {step.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
