/**
 * Summary: Pure SSE and snapshot reduction for the investigation client state.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - caseStepReady: report whether a wizard step has renderable evidence.
 * - initialInvestigationState: construct the empty state.
 * - reduceInvestigation: fold one SSE frame into state.
 * - investigationStateFromSnapshot: reconcile the authoritative saved snapshot.
 *
 * Notes:
 * - Unknown payloads degrade to safe defaults rather than throwing in render paths.
 */
import type { SseMessage } from "./sse";
import type {
  AgentRun,
  AgentRunStatus,
  CaseStepKey,
  InvestigationRuleHit,
  InvestigationSnapshotData,
  InvestigationState,
  InvestigationStatus,
  RegulationCitation,
  ShapFeature,
} from "./investigationTypes";

export function caseStepReady(state: InvestigationState, key: CaseStepKey): boolean {
  switch (key) {
    case "risk":
      return (
        state.completedSteps.includes("scoring") ||
        state.riskBand !== undefined ||
        state.fraudProbability !== undefined
      );
    case "drivers":
      return state.topFeatures.length > 0 || state.completedSteps.includes("shap");
    case "citations":
      return state.completedSteps.includes("rag");
    case "sar":
      return state.sarStarted || state.sarText.length > 0 || state.completedSteps.includes("sar");
    case "submit":
    case "outcome":
      return state.status === "completed";
    default:
      return false;
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function numberOf(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringOf(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function ruleHitsOf(value: unknown): InvestigationRuleHit[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((raw) => {
    const record = asRecord(raw);
    return {
      code: stringOf(record.code) ?? "",
      ruleType: stringOf(record.ruleType) ?? "",
      severity: stringOf(record.severity) ?? "",
      reason: stringOf(record.reason) ?? "",
    };
  });
}

function featuresOf(value: unknown): ShapFeature[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((raw) => {
    const record = asRecord(raw);
    return {
      feature: stringOf(record.feature) ?? "",
      value: numberOf(record.value) ?? 0,
      shapValue: numberOf(record.shapValue) ?? 0,
    };
  });
}

function citationsOf(value: unknown): RegulationCitation[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((raw) => {
    const record = asRecord(raw);
    return {
      citation: stringOf(record.citation) ?? "",
      title: stringOf(record.title) ?? "",
      source: stringOf(record.source) ?? "",
      snippet: stringOf(record.snippet) ?? "",
    };
  });
}

function agentStatusOf(value: unknown, fallback: AgentRunStatus): AgentRunStatus {
  switch (value) {
    case "pending":
    case "started":
    case "running":
    case "revision_requested":
    case "completed":
    case "degraded":
    case "failed":
    case "skipped":
      return value;
    default:
      return fallback;
  }
}

type AgentRunUpdate = Pick<AgentRun, "agentRunId" | "agent" | "attempt"> &
  Partial<Omit<AgentRun, "agentRunId" | "agent" | "attempt">>;

function upsertAgentRun(runs: AgentRun[], update: AgentRunUpdate): AgentRun[] {
  const index = runs.findIndex((run) => run.agentRunId === update.agentRunId);
  const previous = index >= 0 ? runs[index] : undefined;
  const merged: AgentRun = {
    ...previous,
    ...update,
    agentRunId: update.agentRunId,
    agent: update.agent,
    attempt: update.attempt,
    status: update.status ?? previous?.status ?? "pending",
    toolCalls: update.toolCalls ?? previous?.toolCalls ?? [],
  };
  if (index < 0) {
    return [...runs, merged];
  }
  return runs.map((run, runIndex) => (runIndex === index ? merged : run));
}

function agentEventUpdate(
  runs: AgentRun[],
  data: Record<string, unknown>,
  fallbackStatus: AgentRunStatus,
  revisionRequested = false,
  usePayloadStatus = true,
): AgentRun[] {
  const agentRunId = stringOf(data.agentRunId);
  const agent = stringOf(data.agent);
  const attempt = numberOf(data.attempt);
  if (!agentRunId || !agent || attempt === undefined) {
    return runs;
  }
  const previous = runs.find((run) => run.agentRunId === agentRunId);
  const toolName = stringOf(data.toolName);
  const toolCalls = toolName
    ? [
        ...(previous?.toolCalls ?? []),
        { name: toolName, status: stringOf(data.status) ?? "completed" },
      ]
    : undefined;
  return upsertAgentRun(runs, {
    agentRunId,
    agent,
    attempt,
    status: usePayloadStatus
      ? agentStatusOf(data.status, fallbackStatus)
      : (previous?.status ?? fallbackStatus),
    errorCode: stringOf(data.errorCode),
    toolCalls,
    revisionRequested: revisionRequested || previous?.revisionRequested,
  });
}

function withStep(steps: string[], key: string): string[] {
  return steps.includes(key) ? steps : [...steps, key];
}

export function initialInvestigationState(): InvestigationState {
  return {
    status: "pending",
    attempt: 0,
    maxAttempts: 1,
    completedSteps: [],
    ruleHits: [],
    erroredRules: [],
    wasCanary: false,
    topFeatures: [],
    citations: [],
    sarStarted: false,
    sarText: "",
    workflowMode: "single_writer",
    revisionCount: 0,
    agentRuns: [],
    recorded: false,
    lastEventId: "",
  };
}

export function reduceInvestigation(
  state: InvestigationState,
  message: SseMessage,
): InvestigationState {
  const data = asRecord(message.data);
  const base: InvestigationState = {
    ...state,
    lastEventId: message.lastEventId || state.lastEventId,
  };
  switch (message.type) {
    case "run.started":
      return {
        ...base,
        status: "running",
        transactionId: stringOf(data.transactionId),
        attempt: numberOf(data.attempt) ?? state.attempt,
        maxAttempts: numberOf(data.maxAttempts) ?? state.maxAttempts,
      };
    case "step.rules.completed":
      return {
        ...base,
        subscore: numberOf(data.subscore),
        rulesVersion: stringOf(data.rulesVersion),
        ruleHits: ruleHitsOf(data.ruleHits),
        erroredRules: stringArray(data.erroredRules),
        completedSteps: withStep(state.completedSteps, "rules"),
      };
    case "step.scoring.completed":
      return {
        ...base,
        fraudProbability: numberOf(data.fraudProbability),
        modelVersion: stringOf(data.modelVersion),
        wasCanary: data.wasCanary === true,
        completedSteps: withStep(state.completedSteps, "scoring"),
      };
    case "step.shap.completed":
      return {
        ...base,
        baseValue: numberOf(data.baseValue),
        topFeatures: featuresOf(data.topFeatures),
        completedSteps: withStep(state.completedSteps, "shap"),
      };
    case "step.rag.completed":
      return {
        ...base,
        ragMode: stringOf(data.mode),
        ragVersion: stringOf(data.ragVersion),
        citations: citationsOf(data.citations),
        completedSteps: withStep(state.completedSteps, "rag"),
      };
    case "sar.started":
      return { ...base, sarStarted: true };
    case "sar.token":
      return { ...base, sarText: state.sarText + (stringOf(data.token) ?? "") };
    case "agent.started":
      return {
        ...base,
        workflowMode: "multi_agent",
        agentRuns: agentEventUpdate(state.agentRuns, data, "running"),
      };
    case "agent.tool.completed":
      return {
        ...base,
        workflowMode: "multi_agent",
        agentRuns: agentEventUpdate(state.agentRuns, data, "running", false, false),
      };
    case "agent.completed":
      return {
        ...base,
        workflowMode: "multi_agent",
        agentRuns: agentEventUpdate(state.agentRuns, data, "completed"),
      };
    case "agent.revision.requested":
      return {
        ...base,
        workflowMode: "multi_agent",
        revisionCount: Math.max(state.revisionCount, (numberOf(data.attempt) ?? 1) - 1),
        agentRuns: agentEventUpdate(state.agentRuns, data, "revision_requested", true),
      };
    case "run.completed":
      return {
        ...base,
        status: stringOf(data.sarStatus) === "failed" ? "drafting-blocked" : "completed",
        riskScore: numberOf(data.riskScore),
        riskBand: stringOf(data.riskBand),
        modelVersion: stringOf(data.modelVersion) ?? state.modelVersion,
        sarDraftId: stringOf(data.sarDraftId),
        sarStatus: stringOf(data.sarStatus),
        draftingBlockReason:
          stringOf(data.sarStatus) === "failed" ? "SAR drafting failed" : undefined,
        alertId: stringOf(data.alertId),
        completedSteps: withStep(state.completedSteps, "sar"),
      };
    case "run.failed":
      return { ...base, status: "failed", errorCode: stringOf(data.code) };
    default:
      return base;
  }
}

export function investigationStateFromSnapshot(
  snapshot: InvestigationSnapshotData,
  current: InvestigationState = initialInvestigationState(),
): InvestigationState {
  const snapshotSteps: string[] = [];
  if (snapshot.ruleHits.length > 0) {
    snapshotSteps.push("rules");
  }
  if (snapshot.fraudProbability !== null) {
    snapshotSteps.push("scoring");
  }
  if (snapshot.topFeatures.length > 0) {
    snapshotSteps.push("shap");
  }
  if (snapshot.citations.length > 0) {
    snapshotSteps.push("rag");
  }
  if (snapshot.sarDraftId !== null) {
    snapshotSteps.push("sar");
  }
  const status: InvestigationStatus =
    snapshot.status === "completed"
      ? snapshot.sarStatus === "failed"
        ? "drafting-blocked"
        : "completed"
      : snapshot.status === "failed"
        ? "failed"
        : snapshot.status === "retrying"
          ? "retrying"
          : snapshot.status === "pending"
            ? "pending"
            : "running";
  const agentRuns = snapshot.agentExecutions.reduce(
    (runs, execution) => upsertAgentRun(runs, execution),
    current.agentRuns,
  );
  const completedSteps = snapshotSteps.reduce(withStep, current.completedSteps);
  const terminalAlreadyObserved =
    current.status === "completed" ||
    current.status === "failed" ||
    current.status === "drafting-blocked";
  return {
    ...current,
    status: terminalAlreadyObserved ? current.status : status,
    attempt: snapshot.attempt,
    maxAttempts: snapshot.maxAttempts,
    completedSteps,
    transactionId: snapshot.transactionId,
    ruleHits: current.ruleHits.length > 0 ? current.ruleHits : snapshot.ruleHits,
    rulesVersion: current.rulesVersion ?? snapshot.rulesVersion ?? undefined,
    fraudProbability: current.fraudProbability ?? snapshot.fraudProbability ?? undefined,
    modelVersion: current.modelVersion ?? snapshot.modelVersion ?? undefined,
    topFeatures: current.topFeatures.length > 0 ? current.topFeatures : snapshot.topFeatures,
    citations: current.citations.length > 0 ? current.citations : snapshot.citations,
    ragVersion: current.ragVersion ?? snapshot.ragVersion ?? undefined,
    riskScore: current.riskScore ?? snapshot.riskScore ?? undefined,
    riskBand: current.riskBand ?? snapshot.riskBand ?? undefined,
    sarDraftId: terminalAlreadyObserved ? current.sarDraftId : (snapshot.sarDraftId ?? undefined),
    sarStatus: terminalAlreadyObserved ? current.sarStatus : (snapshot.sarStatus ?? undefined),
    sarText: current.sarText || snapshot.sarContent || "",
    sarStarted: current.sarStarted || snapshot.sarDraftId !== null,
    alertId: terminalAlreadyObserved ? current.alertId : (snapshot.alertId ?? undefined),
    workflowMode:
      current.workflowMode === "multi_agent" ? current.workflowMode : snapshot.workflowMode,
    graphVersion: snapshot.graphVersion ?? undefined,
    revisionCount: snapshot.revisionCount,
    agentRuns,
    recorded: current.recorded || agentRuns.some((run) => run.modelId === "mock"),
    errorCode: snapshot.errorCode ?? undefined,
    draftingBlockReason:
      status === "drafting-blocked"
        ? "SAR drafting failed; manual drafting is required"
        : undefined,
  };
}
