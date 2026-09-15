/**
 * Summary: Shared server-sent-event harness for investigation page tests.
 * Key classes: (none)
 * Key functions: streamHarness, runToCompletion, emitReadyRun, emitNoAlertRun.
 * Notes: Events are delivered inside React act() so assertions observe settled state.
 */

import { act } from "@testing-library/react";
import { vi } from "vitest";

import type { SseClientOptions, SseHandle } from "../lib/sse";

export function streamHarness() {
  let options: SseClientOptions | undefined;
  const close = vi.fn();
  const factory = (received: SseClientOptions): SseHandle => {
    options = received;
    return { close };
  };
  const emit = (type: string, data: unknown, lastEventId = ""): void => {
    act(() => options?.onMessage({ type, data, lastEventId }));
  };
  const fail = (): void => {
    act(() => options?.onError?.(new Event("error")));
  };
  return { factory, emit, fail, close };
}

export function runToCompletion(harness: ReturnType<typeof streamHarness>): void {
  harness.emit("run.started", { transactionId: "tx-1" }, "1");
  harness.emit("step.rules.completed", {
    ruleHits: [
      { code: "STRUCTURING", ruleType: "structuring", severity: "high", reason: "near threshold" },
    ],
  });
  harness.emit("step.scoring.completed", { fraudProbability: 0.9, modelVersion: "m1" }, "3");
}

export function emitReadyRun(
  harness: ReturnType<typeof streamHarness>,
  alertId: string | null = "alert-1",
  sarStatus = "draft",
): void {
  runToCompletion(harness);
  harness.emit("step.shap.completed", {
    topFeatures: [{ feature: "amount", value: 1, shapValue: 0.5 }],
  });
  harness.emit("step.rag.completed", {
    mode: "vector",
    citations: [{ citation: "c", title: "t", source: "FinCEN", snippet: "s" }],
  });
  harness.emit("sar.started", {}, "6");
  harness.emit("sar.token", { token: "Narrative ready for review." });
  harness.emit(
    "run.completed",
    { riskScore: 0.87, riskBand: "high", sarDraftId: "s1", sarStatus, alertId },
    "7",
  );
}

export function emitNoAlertRun(harness: ReturnType<typeof streamHarness>): void {
  runToCompletion(harness);
  harness.emit("step.shap.completed", {
    topFeatures: [{ feature: "amount_log", value: 9.9, shapValue: -0.4 }],
  });
  harness.emit("step.rag.completed", {
    mode: "vector",
    citations: [{ citation: "legacy", title: "legacy", source: "FinCEN", snippet: "legacy" }],
  });
  harness.emit(
    "run.completed",
    { riskScore: 0.22, riskBand: "low", sarDraftId: null, sarStatus: null, alertId: null },
    "5",
  );
}
