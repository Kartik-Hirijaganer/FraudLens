import { describe, expect, it } from "vitest";

import {
  CASE_STEPS,
  INVESTIGATION_EVENTS,
  caseStepReady,
  initialInvestigationState,
  reduceInvestigation,
  type InvestigationState,
} from "./investigation";
import type { SseMessage } from "./sse";

function msg(type: string, data: unknown, lastEventId = ""): SseMessage {
  return { type, data, lastEventId };
}

function fold(messages: SseMessage[]): InvestigationState {
  return messages.reduce(reduceInvestigation, initialInvestigationState());
}

describe("investigation constants", () => {
  it("subscribes to all nine stream events and five wizard steps", () => {
    expect(INVESTIGATION_EVENTS).toContain("sar.token");
    expect(INVESTIGATION_EVENTS).toHaveLength(9);
    expect(CASE_STEPS.map((step) => step.key)).toEqual([
      "risk",
      "drivers",
      "citations",
      "sar",
      "submit",
    ]);
  });
});

describe("caseStepReady", () => {
  it("maps the auto-run pipeline stages onto wizard-step readiness", () => {
    const empty = initialInvestigationState();
    expect(caseStepReady(empty, "risk")).toBe(false);
    expect(caseStepReady(empty, "submit")).toBe(false);

    const scored = fold([
      msg("run.started", { transactionId: "tx-1" }),
      msg("step.scoring.completed", { fraudProbability: 0.9, modelVersion: "m1" }),
    ]);
    expect(caseStepReady(scored, "risk")).toBe(true);
    expect(caseStepReady(scored, "drivers")).toBe(false);

    const done = fold([
      msg("step.shap.completed", { topFeatures: [{ feature: "amount", value: 1, shapValue: 1 }] }),
      msg("step.rag.completed", { mode: "vector", citations: [] }),
      msg("sar.started", {}),
      msg("run.completed", { riskScore: 0.8, riskBand: "high", sarDraftId: "s1" }),
    ]);
    expect(caseStepReady(done, "drivers")).toBe(true);
    expect(caseStepReady(done, "citations")).toBe(true);
    expect(caseStepReady(done, "sar")).toBe(true);
    expect(caseStepReady(done, "submit")).toBe(true);
  });

  it("treats each evidence signal as sufficient for its wizard step", () => {
    const base = initialInvestigationState();
    // Risk is ready from the band or the probability alone (not only from a completed step).
    expect(caseStepReady({ ...base, riskBand: "high" }, "risk")).toBe(true);
    expect(caseStepReady({ ...base, fraudProbability: 0.5 }, "risk")).toBe(true);
    // Drivers/sar are ready via the completed-step marker too, not only their data.
    expect(caseStepReady({ ...base, completedSteps: ["shap"] }, "drivers")).toBe(true);
    expect(caseStepReady({ ...base, sarText: "x" }, "sar")).toBe(true);
    expect(caseStepReady({ ...base, completedSteps: ["sar"] }, "sar")).toBe(true);
  });
});

describe("reduceInvestigation", () => {
  it("folds a full successful run into terminal state", () => {
    const state = fold([
      msg("run.started", { transactionId: "tx-1" }, "1"),
      msg(
        "step.rules.completed",
        {
          subscore: 0.4,
          rulesVersion: "rules-v1",
          ruleHits: [
            { code: "STRUCTURING", ruleType: "structuring", severity: "high", reason: "r" },
          ],
          erroredRules: ["bad_rule"],
        },
        "2",
      ),
      msg(
        "step.scoring.completed",
        { fraudProbability: 0.92, modelVersion: "m1", wasCanary: true },
        "3",
      ),
      msg(
        "step.shap.completed",
        { baseValue: 0.1, topFeatures: [{ feature: "amount", value: 5, shapValue: 0.3 }] },
        "4",
      ),
      msg(
        "step.rag.completed",
        {
          mode: "vector",
          ragVersion: "rag-v1",
          citations: [{ citation: "31 CFR", title: "T", source: "FinCEN", snippet: "s" }],
        },
        "5",
      ),
      msg("sar.started", {}, "6"),
      msg("sar.token", { token: "Hello " }),
      msg("sar.token", { token: "world" }),
      msg(
        "run.completed",
        { riskScore: 0.81, riskBand: "critical", modelVersion: "m1", sarDraftId: "sar-1" },
        "7",
      ),
    ]);

    expect(state.status).toBe("completed");
    expect(state.transactionId).toBe("tx-1");
    expect(state.completedSteps).toEqual(["rules", "scoring", "shap", "rag", "sar"]);
    expect(state.ruleHits[0].code).toBe("STRUCTURING");
    expect(state.erroredRules).toEqual(["bad_rule"]);
    expect(state.fraudProbability).toBe(0.92);
    expect(state.wasCanary).toBe(true);
    expect(state.topFeatures[0].shapValue).toBe(0.3);
    expect(state.citations[0].source).toBe("FinCEN");
    expect(state.ragMode).toBe("vector");
    expect(state.sarStarted).toBe(true);
    expect(state.sarText).toBe("Hello world");
    expect(state.riskBand).toBe("critical");
    expect(state.sarDraftId).toBe("sar-1");
    expect(state.lastEventId).toBe("7");
  });

  it("records a failed run with its code", () => {
    const state = fold([
      msg("run.started", { transactionId: "tx-2" }, "1"),
      msg("run.failed", { code: "investigation_failed" }, "2"),
    ]);
    expect(state.status).toBe("failed");
    expect(state.errorCode).toBe("investigation_failed");
  });

  it("degrades to safe defaults for malformed payloads", () => {
    const state = fold([
      msg("step.rules.completed", null),
      msg("step.shap.completed", { topFeatures: "not-an-array" }),
      msg("step.rag.completed", { citations: 42 }),
    ]);
    expect(state.ruleHits).toEqual([]);
    expect(state.topFeatures).toEqual([]);
    expect(state.citations).toEqual([]);
  });

  it("ignores an unknown event but keeps the last persisted id", () => {
    const state = reduceInvestigation(initialInvestigationState(), msg("noise", {}, "9"));
    expect(state.status).toBe("starting");
    expect(state.lastEventId).toBe("9");
  });

  it("de-dupes repeated steps and tolerates wrong-typed payload fields", () => {
    const state = fold([
      msg("step.rules.completed", { ruleHits: [], subscore: 0.1 }, "1"),
      msg("step.rules.completed", { ruleHits: [], subscore: 0.2 }, "2"),
      msg("step.scoring.completed", { fraudProbability: "nope", modelVersion: 5 }, "3"),
    ]);
    expect(state.completedSteps).toEqual(["rules", "scoring"]);
    expect(state.fraudProbability).toBeUndefined();
    expect(state.modelVersion).toBeUndefined();
  });
});
