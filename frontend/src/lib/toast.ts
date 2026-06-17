/**
 * Summary: Thin wrapper over Sonner toasts (plan §13, §16 Phase 11) that centralizes
 * the FraudLens notification policy: tones map onto the semantic palette (positive /
 * warning / negative / neutral — never the brand green), the timeout is configurable,
 * and CRITICAL toasts persist (no auto-dismiss) until the analyst acknowledges them.
 * `notifyError` routes any thrown value through `describeError` so a toast only ever
 * shows safe, PHI-free copy. Keeping this in one place means components never call
 * Sonner directly (rule 5: no duplication) and the policy is tested once.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - DEFAULT_TOAST_DURATION_MS: the default non-critical auto-dismiss timeout (ms).
 * - notify: show a toast applying the tone -> Sonner-variant + critical-persist policy.
 * - notifyError: describe a thrown value and show it as a (possibly critical) toast.
 *
 * Notes:
 * - DEFAULT_TOAST_DURATION_MS is the non-critical default; `critical` overrides it with
 * Infinity (Sonner's "stay until dismissed").
 */
import { toast } from "sonner";

import { describeError } from "./errors";
import type { StatusTone } from "./risk";

export const DEFAULT_TOAST_DURATION_MS = 5000;

interface NotifyOptions {
  tone?: StatusTone;
  title: string;
  description?: string;
  durationMs?: number;
  critical?: boolean;
}

const VARIANTS: Record<StatusTone, (typeof toast)["message"]> = {
  positive: toast.success,
  warning: toast.warning,
  negative: toast.error,
  neutral: toast.message,
};

export function notify(options: NotifyOptions): string | number {
  const { tone = "neutral", title, description, durationMs, critical = false } = options;
  const duration = critical ? Infinity : (durationMs ?? DEFAULT_TOAST_DURATION_MS);
  return VARIANTS[tone](title, { description, duration });
}

export function notifyError(error: unknown): string | number {
  const described = describeError(error);
  return notify({
    tone: "negative",
    title: described.title,
    description: described.description,
    critical: described.critical,
  });
}
