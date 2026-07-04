/**
 * Summary: The horizontal "build the case" step tracker (redesigned investigation page).
 * It renders the ordered wizard steps (`CASE_STEPS`: Risk → Drivers → Citations → SAR
 * draft → Submit) as numbered markers joined by connectors, deriving each marker's state
 * from the analyst's position (`currentStep`): steps before it are done (dark ✓), the one
 * at it is active (brand marker + number), later ones are pending (sage + number). The
 * connector leading into the active step echoes the active accent so the eye lands on the
 * step being worked; the active marker carries `aria-current="step"`.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - CaseStepper: render the ordered wizard step markers + connectors.
 *
 * Notes:
 * - Display-only (back/forward navigation lives in the page footer + CTA), so it takes no
 *   handlers; each step is an equal-width column so labels sit centred under their marker.
 * - The brand accent marks the active step (the one the analyst is acting on), mirroring the
 *   step's green primary CTA; every other colour comes from the neutral ink/sage palette.
 */
import { cx } from "../lib/cx";
import type { CaseStepKey } from "../lib/investigation";

interface CaseStep {
  key: CaseStepKey;
  label: string;
}

interface CaseStepperProps {
  steps: readonly CaseStep[];
  currentStep: number;
}

type MarkerState = "done" | "active" | "pending";

const MARKER_CLASSES: Record<MarkerState, string> = {
  done: "bg-ink text-canvas",
  active: "bg-primary text-on-primary",
  pending: "bg-canvas-soft text-mute",
};

const LABEL_CLASSES: Record<MarkerState, string> = {
  done: "text-ink",
  active: "text-ink",
  pending: "text-mute",
};

function markerState(index: number, currentStep: number): MarkerState {
  if (index < currentStep) {
    return "done";
  }
  if (index === currentStep) {
    return "active";
  }
  return "pending";
}

// The connector between step `index-1` and `index`: dark once cleared, brand while it leads
// into the active step, sage while still ahead.
function connectorClass(index: number, currentStep: number): string {
  if (index < currentStep) {
    return "bg-ink";
  }
  if (index === currentStep) {
    return "bg-primary";
  }
  return "bg-canvas-soft";
}

export function CaseStepper({ steps, currentStep }: CaseStepperProps) {
  return (
    <ol className="flex items-start">
      {steps.map((step, index) => {
        const state = markerState(index, currentStep);
        return (
          <li
            key={step.key}
            aria-current={state === "active" ? "step" : undefined}
            className="gap-sm flex flex-1 flex-col items-center"
          >
            <div className="flex w-full items-center">
              <span
                aria-hidden="true"
                className={cx(
                  "h-xxs flex-1 rounded-pill",
                  index === 0 ? "bg-transparent" : connectorClass(index, currentStep),
                )}
              />
              <span
                aria-hidden="true"
                className={cx(
                  "text-body-sm flex size-2xl shrink-0 items-center justify-center rounded-full font-semibold",
                  MARKER_CLASSES[state],
                )}
              >
                {state === "done" ? "✓" : index + 1}
              </span>
              <span
                aria-hidden="true"
                className={cx(
                  "h-xxs flex-1 rounded-pill",
                  index === steps.length - 1
                    ? "bg-transparent"
                    : connectorClass(index + 1, currentStep),
                )}
              />
            </div>
            <span
              className={cx(
                "px-xxs text-body-sm text-center font-semibold leading-tight",
                LABEL_CLASSES[state],
              )}
            >
              {step.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
