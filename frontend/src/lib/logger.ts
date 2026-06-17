/**
 * Summary: The client-side error reporter (plan §5.3 endpoint 27, §8.4, §16 Phase 11
 * `lib/logger.ts`). It posts a PHI-scrubbed message + safe context to the gateway's
 * `/api/v1/telemetry/client-error` sink so frontend faults are observable without ever
 * shipping PHI off the browser: `scrubForLog` masks long digit runs (account/card-like)
 * and emails before sending, and the backend scrubs again as defense-in-depth. Reporting
 * is best-effort — a failed post is swallowed so logging can never cascade into a second
 * error or block the UI. `installErrorReporter` wires `window` error / unhandledrejection
 * to the sink.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - scrubForLog: mask PHI-shaped tokens (long digit runs, emails) from a log string.
 * - reportClientError: POST a scrubbed message + context to the client-error sink.
 * - installErrorReporter: forward window error / unhandledrejection events to the sink.
 *
 * Notes:
 * - The browser scrub is a safety net, not the authority — the gateway sink re-scrubs and
 * rate-limits. Context values are coerced to strings and scrubbed the same way.
 */
import { config } from "./config";

const DIGIT_RUN = /\d{7,}/g;
const EMAIL = /[^\s@]+@[^\s@]+\.[^\s@]+/g;
const MASK = "[redacted]";

export function scrubForLog(text: string): string {
  return text.replace(EMAIL, MASK).replace(DIGIT_RUN, MASK);
}

interface ReportOptions {
  fetchImpl?: typeof fetch;
  baseUrl?: string;
}

export async function reportClientError(
  message: string,
  context?: Record<string, string>,
  options: ReportOptions = {},
): Promise<void> {
  const { fetchImpl = fetch, baseUrl = config.apiBaseUrl } = options;
  const scrubbedContext = context
    ? Object.fromEntries(
        Object.entries(context).map(([key, value]) => [key, scrubForLog(String(value))]),
      )
    : undefined;
  try {
    await fetchImpl(`${baseUrl}/api/v1/telemetry/client-error`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: scrubForLog(message), context: scrubbedContext }),
    });
  } catch {
    // Best-effort: never let reporting an error raise another one.
  }
}

export function installErrorReporter(
  target: Window = window,
  options: ReportOptions = {},
): () => void {
  const onError = (event: ErrorEvent): void => {
    void reportClientError(event.message, { kind: "error" }, options);
  };
  const onRejection = (event: PromiseRejectionEvent): void => {
    void reportClientError(String(event.reason), { kind: "unhandledRejection" }, options);
  };
  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}
