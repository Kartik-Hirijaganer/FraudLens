import { describe, expect, it } from "vitest";

import {
  CASE_STEPS,
  INVESTIGATION_EVENTS,
  NO_ALERT_CASE_STEPS,
  caseStepReady,
  initialInvestigationState,
  investigationStateFromSnapshot,
  investigationTimeline,
  reduceInvestigation,
  type InvestigationState,
} from "./investigation";
import type { SseMessage } from "./sse";
import { agentRun, snapshot } from "../test/factories";

function msg(type: string, data: unknown, lastEventId = ""): SseMessage {
  return { type, data, lastEventId };
}

function fold(messages: SseMessage[]): InvestigationState {
  return messages.reduce(reduceInvestigation, initialInvestigationState());
}

describe("investigation constants", () => {
  it("subscribes to all pipeline and agent stream events and five wizard steps", () => {
    expect(INVESTIGATION_EVENTS).toContain("sar.token");
    expect(INVESTIGATION_EVENTS).toEqual(
      expect.arrayContaining([
        "agent.started",
        "agent.tool.completed",
        "agent.completed",
        "agent.revision.requested",
      ]),
    );
    expect(INVESTIGATION_EVENTS).toHaveLength(13);
    expect(CASE_STEPS.map((step) => step.key)).toEqual([
      "risk",
      "drivers",
      "citations",
      "sar",
      "submit",
    ]);
    expect(NO_ALERT_CASE_STEPS.map((step) => step.key)).toEqual(["risk", "drivers", "outcome"]);
  });
});

describe("caseStepReady", () => {
  it("maps the auto-run pipeline stages onto wizard-step readiness", () => {
    const empty = initialInvestigationState();
    expect(caseStepReady(empty, "risk")).toBe(false);
    expect(caseStepReady(empty, "submit")).toBe(false);
    expect(caseStepReady(empty, "outcome")).toBe(false);

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
      msg("run.completed", {
        riskScore: 0.8,
        riskBand: "high",
        sarDraftId: "s1",
        sarStatus: "draft",
        alertId: "alert-1",
      }),
    ]);
    expect(caseStepReady(done, "drivers")).toBe(true);
    expect(caseStepReady(done, "citations")).toBe(true);
    expect(caseStepReady(done, "sar")).toBe(true);
    expect(caseStepReady(done, "submit")).toBe(true);
    expect(caseStepReady(done, "outcome")).toBe(true);
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
        {
          riskScore: 0.81,
          riskBand: "critical",
          modelVersion: "m1",
          sarDraftId: "sar-1",
          sarStatus: "draft",
          alertId: "alert-1",
        },
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
    expect(state.sarStatus).toBe("draft");
    expect(state.alertId).toBe("alert-1");
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

  it("handles every agent event and keys revision attempts by agentRunId", () => {
    const state = fold([
      msg("agent.started", {
        agentRunId: "writer-1",
        agent: "sar_writer",
        attempt: 1,
        status: "started",
      }),
      msg("agent.tool.completed", {
        agentRunId: "writer-1",
        agent: "sar_writer",
        attempt: 1,
        status: "completed",
        toolName: "rule_hits",
      }),
      msg("agent.completed", {
        agentRunId: "writer-1",
        agent: "sar_writer",
        attempt: 1,
        status: "degraded",
        errorCode: "provider_timeout",
      }),
      msg("agent.revision.requested", {
        agentRunId: "writer-2",
        agent: "sar_writer",
        attempt: 2,
        status: "revision_requested",
      }),
      msg("agent.started", {
        agentRunId: "writer-2",
        agent: "sar_writer",
        attempt: 2,
        status: "started",
      }),
      msg("agent.completed", {
        agentRunId: "writer-2",
        agent: "sar_writer",
        attempt: 2,
        status: "completed",
      }),
    ]);

    expect(state.workflowMode).toBe("multi_agent");
    expect(state.revisionCount).toBe(1);
    expect(state.agentRuns).toHaveLength(2);
    expect(state.agentRuns[0]).toMatchObject({
      agentRunId: "writer-1",
      status: "degraded",
      toolCalls: [{ name: "rule_hits", status: "completed" }],
    });
    expect(state.agentRuns[1]).toMatchObject({
      agentRunId: "writer-2",
      attempt: 2,
      status: "completed",
      revisionRequested: true,
    });
  });

  it("builds the same timeline from live delivery and a fresh persisted replay", () => {
    const sequence = [
      msg("run.started", { transactionId: "tx-1" }, "1"),
      msg("step.rules.completed", { ruleHits: [] }, "2"),
      msg("step.scoring.completed", { fraudProbability: 0.9 }, "3"),
      msg(
        "agent.started",
        {
          agentRunId: "evidence-1",
          agent: "evidence_investigator",
          attempt: 1,
          status: "started",
        },
        "4",
      ),
      msg(
        "agent.completed",
        {
          agentRunId: "evidence-1",
          agent: "evidence_investigator",
          attempt: 1,
          status: "completed",
        },
        "5",
      ),
    ];
    const live = sequence.reduce(reduceInvestigation, initialInvestigationState());
    const replay = fold(sequence);

    expect(investigationTimeline(live)).toEqual(investigationTimeline(replay));
  });

  it("restores persisted SAR content and agent provenance through the snapshot path", () => {
    const state = investigationStateFromSnapshot(
      snapshot({
        workflowMode: "multi_agent",
        graphVersion: "agents-v1",
        revisionCount: 1,
        sarContent: "Persisted narrative.",
        agentExecutions: [agentRun()],
      }),
    );

    expect(state.sarText).toBe("Persisted narrative.");
    expect(state.recorded).toBe(true);
    expect(investigationTimeline(state)[2]).toMatchObject({
      label: "Parallel investigation",
      children: [expect.objectContaining({ label: "Evidence investigator" }), expect.any(Object)],
    });
  });

  it("marks an unexecuted reviewer skipped after a writer failure", () => {
    const failedWriter = agentRun({
      agent: "sar_writer",
      status: "failed",
      errorCode: "writer_schema_invalid",
    });
    const timeline = investigationTimeline({
      ...initialInvestigationState(),
      workflowMode: "multi_agent",
      agentRuns: [failedWriter],
    });

    expect(timeline.find((row) => row.label === "Compliance reviewer")?.status).toBe("skipped");
  });
});
