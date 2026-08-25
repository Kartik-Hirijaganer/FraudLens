/**
 * Summary: The PHI-free client model of a streamed investigation (plan §5.4, §10.2,
 * §16 Phase 11). `INVESTIGATION_EVENTS` is the exact set of server-sent event names the
 * SSE client subscribes to; `reduceInvestigation` folds each parsed `SseMessage` into an
 * accumulating `InvestigationState` (which steps are done, the score/band, SHAP drivers,
 * citations, the streamed SAR text, the terminal outcome). Keeping the fold here — pure,
 * no React, no IO — makes the live stream deterministic and unit-testable, and lets the
 * Investigation page and `ProgressSteps` share one definition of the step order (rule 5).
 *
 * Key classes:
 * - AgentToolCall:
 * - AgentRun: one stable agent attempt assembled from live/replayed events or a snapshot.
 * - AgentTimelineRow: one render-ready machine-progress row (optionally with parallel children).
 * - ShapFeature: one SHAP driver (feature name + value + signed contribution).
 * - RegulationCitation: one grounded regulatory citation (id + title + source + snippet).
 * - InvestigationRuleHit: one fired deterministic rule (code + type + severity + reason).
 * - InvestigationState: the accumulated UI state of a run.
 * - InvestigationSnapshotData:
 *
 * Key functions:
 * - INVESTIGATION_EVENTS: the named server-sent events to subscribe to.
 * - CASE_STEPS: the ordered "build the case" wizard steps (Risk → … → Submit).
 * - NO_ALERT_CASE_STEPS:
 * - caseStepReady: whether a wizard step's evidence has arrived in the streamed state (pure).
 * - upsertAgentRun: merge one agent attempt by agentRunId (never by role).
 * - initialInvestigationState: the empty starting state for a run.
 * - reduceInvestigation: fold one SSE message into the next state (pure).
 * - investigationStateFromSnapshot: reconcile an authoritative saved snapshot.
 * - investigationTimeline: build the single- or multi-agent machine-progress timeline (pure).
 *
 * Notes:
 * - Every payload field is read defensively from `unknown` (a malformed frame degrades to
 * safe defaults), so a bad event can never throw inside the live render path.
 * - The five auto-run pipeline stages (rules/scoring/shap/rag/sar, from the SSE events)
 * are collapsed by `caseStepReady` into the analyst-facing wizard steps in `CASE_STEPS`,
 * so the page and stepper share one definition of the step order (rule 5).
 */
import type { SseMessage } from "./sse";

export type InvestigationStatus = "starting" | "running" | "completed" | "failed";
export type WorkflowMode = "single_writer" | "multi_agent";
export type AgentRunStatus =
  | "pending"
  | "started"
  | "running"
  | "revision_requested"
  | "completed"
  | "degraded"
  | "failed"
  | "skipped";
export type AgentTimelineStatus =
  | "pending"
  | "running"
  | "revision_requested"
  | "completed"
  | "degraded"
  | "failed"
  | "skipped"
  | "awaiting";

export interface AgentToolCall {
  callId?: string;
  name: string;
  arguments?: Record<string, unknown>;
  status: string;
  errorCode?: string | null;
  result?: Record<string, unknown> | null;
}

export interface AgentRun {
  agentRunId: string;
  agent: string;
  attempt: number;
  status: AgentRunStatus;
  errorCode?: string | null;
  modelId?: string;
  promptVersion?: string;
  promptHash?: string;
  inputHash?: string;
  resultHash?: string | null;
  latencyMs?: number;
  modelCallCount?: number;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  costUsd?: string;
  result?: Record<string, unknown> | null;
  toolCalls: AgentToolCall[];
  revisionRequested?: boolean;
}

export interface AgentTimelineRow {
  id: string;
  label: string;
  purpose: string;
  status: AgentTimelineStatus;
  agentRun?: AgentRun;
  children?: AgentTimelineRow[];
}

export interface ShapFeature {
  feature: string;
  value: number;
  shapValue: number;
}

export interface RegulationCitation {
  citation: string;
  title: string;
  source: string;
  snippet: string;
}

export interface InvestigationRuleHit {
  code: string;
  ruleType: string;
  severity: string;
  reason: string;
}

export interface InvestigationState {
  status: InvestigationStatus;
  completedSteps: string[];
  transactionId?: string;
  subscore?: number;
  rulesVersion?: string;
  ruleHits: InvestigationRuleHit[];
  erroredRules: string[];
  fraudProbability?: number;
  modelVersion?: string;
  wasCanary: boolean;
  baseValue?: number;
  topFeatures: ShapFeature[];
  ragMode?: string;
  ragVersion?: string;
  citations: RegulationCitation[];
  sarStarted: boolean;
  sarText: string;
  riskScore?: number;
  riskBand?: string;
  sarDraftId?: string;
  sarStatus?: string;
  alertId?: string;
  workflowMode: WorkflowMode;
  graphVersion?: string;
  revisionCount: number;
  agentRuns: AgentRun[];
  recorded: boolean;
  errorCode?: string;
  lastEventId: string;
}

export interface InvestigationSnapshotData {
  transactionId: string;
  status: string;
  fraudProbability: number | null;
  modelVersion: string | null;
  rulesVersion: string | null;
  ragVersion: string | null;
  topFeatures: ShapFeature[];
  ruleHits: InvestigationRuleHit[];
  citations: RegulationCitation[];
  riskScore: number | null;
  riskBand: string | null;
  sarDraftId: string | null;
  sarStatus: string | null;
  sarContent: string | null;
  alertId: string | null;
  workflowMode: WorkflowMode;
  graphVersion: string | null;
  revisionCount: number;
  agentExecutions: AgentRun[];
  errorCode: string | null;
}

export const INVESTIGATION_EVENTS = [
  "run.started",
  "step.rules.completed",
  "step.scoring.completed",
  "step.shap.completed",
  "step.rag.completed",
  "sar.started",
  "sar.token",
  "agent.started",
  "agent.tool.completed",
  "agent.completed",
  "agent.revision.requested",
  "run.completed",
  "run.failed",
] as const;

export const CASE_STEPS = [
  { key: "risk", label: "Risk" },
  { key: "drivers", label: "Drivers" },
  { key: "citations", label: "Citations" },
  { key: "sar", label: "SAR draft" },
  { key: "submit", label: "Approval" },
] as const;

export const NO_ALERT_CASE_STEPS = [
  { key: "risk", label: "Risk" },
  { key: "drivers", label: "Drivers" },
  { key: "outcome", label: "Outcome" },
] as const;

export type CaseStepKey =
  | (typeof CASE_STEPS)[number]["key"]
  | (typeof NO_ALERT_CASE_STEPS)[number]["key"];

/**
 * Whether the evidence a wizard step displays has arrived in the streamed state. The
 * auto-run pipeline (rules → scoring → shap → rag → sar) drives the underlying
 * `completedSteps`; this collapses those stages onto the analyst-facing wizard steps so
 * the page can gate "continue" until the next step actually has something to show.
 */
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

export function upsertAgentRun(runs: AgentRun[], update: AgentRunUpdate): AgentRun[] {
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
    status: "starting",
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
      return { ...base, status: "running", transactionId: stringOf(data.transactionId) };
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
        status: "completed",
        riskScore: numberOf(data.riskScore),
        riskBand: stringOf(data.riskBand),
        modelVersion: stringOf(data.modelVersion) ?? state.modelVersion,
        sarDraftId: stringOf(data.sarDraftId),
        sarStatus: stringOf(data.sarStatus),
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
      ? "completed"
      : snapshot.status === "failed"
        ? "failed"
        : "running";
  const agentRuns = snapshot.agentExecutions.reduce(
    (runs, execution) => upsertAgentRun(runs, execution),
    current.agentRuns,
  );
  const completedSteps = snapshotSteps.reduce(withStep, current.completedSteps);
  const terminalAlreadyObserved = current.status === "completed" || current.status === "failed";
  return {
    ...current,
    status: terminalAlreadyObserved ? current.status : status,
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
  };
}

const AGENT_PURPOSES: Record<string, string> = {
  evidence_investigator: "Collect the governed transaction, rule, model, and alert evidence.",
  regulatory_analyst: "Match the case evidence to applicable regulatory provisions.",
  sar_writer: "Synthesize only supplied evidence into a traceable SAR narrative.",
  compliance_reviewer: "Check evidence support, citations, materiality, tone, and regulatory fit.",
};

const AGENT_LABELS: Record<string, string> = {
  evidence_investigator: "Evidence investigator",
  regulatory_analyst: "Regulatory analyst",
  sar_writer: "SAR writer",
  compliance_reviewer: "Compliance reviewer",
};

function timelineStatus(
  run: AgentRun | undefined,
  missing: AgentTimelineStatus,
): AgentTimelineStatus {
  if (!run) {
    return missing;
  }
  if (run.status === "started") {
    return "running";
  }
  return run.status;
}

function roleRow(
  run: AgentRun | undefined,
  agent: string,
  attempt: number,
  missing: AgentTimelineStatus,
): AgentTimelineRow {
  const revisionLabel = attempt > 1 ? ` · Revision ${attempt - 1}` : "";
  return {
    id: run?.agentRunId ?? `${agent}-${attempt}`,
    label: `${AGENT_LABELS[agent] ?? agent}${revisionLabel}`,
    purpose: AGENT_PURPOSES[agent] ?? "Execute one bounded workflow role.",
    status: timelineStatus(run, missing),
    agentRun: run,
  };
}

function deterministicStatus(state: InvestigationState, step: string): AgentTimelineStatus {
  if (state.completedSteps.includes(step)) {
    return "completed";
  }
  return state.status === "completed" || state.status === "failed" ? "skipped" : "pending";
}

function singleWriterTimeline(state: InvestigationState): AgentTimelineRow[] {
  const sarStatus: AgentTimelineStatus =
    state.sarStatus === "failed"
      ? "failed"
      : state.completedSteps.includes("sar")
        ? "completed"
        : state.sarStarted
          ? "running"
          : state.status === "completed"
            ? "skipped"
            : "pending";
  const reviewStatus: AgentTimelineStatus =
    state.status === "completed" && state.sarDraftId
      ? "awaiting"
      : state.status === "completed" || state.status === "failed"
        ? "skipped"
        : "pending";
  return [
    {
      id: "rules",
      label: "Rules",
      purpose: "Evaluate deterministic fraud and AML indicators.",
      status: deterministicStatus(state, "rules"),
    },
    {
      id: "risk",
      label: "Risk scored",
      purpose: "Score the transaction and record its model provenance.",
      status: deterministicStatus(state, "scoring"),
    },
    {
      id: "sar",
      label: "SAR drafted",
      purpose: "Draft a report from the scored evidence and grounded regulations.",
      status: sarStatus,
    },
    {
      id: "human-review",
      label: "Awaiting human review",
      purpose: "Keep the report in draft until an authorized reviewer decides.",
      status: reviewStatus,
    },
  ];
}

function groupStatus(children: AgentTimelineRow[]): AgentTimelineStatus {
  const statuses = new Set(children.map((row) => row.status));
  if (statuses.has("running") || statuses.has("revision_requested")) {
    return "running";
  }
  if (statuses.has("failed")) {
    return "failed";
  }
  if (statuses.has("degraded")) {
    return "degraded";
  }
  if ([...statuses].every((status) => status === "completed")) {
    return "completed";
  }
  if ([...statuses].every((status) => status === "skipped")) {
    return "skipped";
  }
  return "pending";
}

export function investigationTimeline(state: InvestigationState): AgentTimelineRow[] {
  if (state.workflowMode !== "multi_agent") {
    return singleWriterTimeline(state);
  }
  const byRoleAttempt = new Map(
    state.agentRuns.map((run) => [`${run.agent}:${run.attempt}`, run] as const),
  );
  const missing: AgentTimelineStatus =
    state.status === "completed" || state.status === "failed" ? "skipped" : "pending";
  const parallel = [
    roleRow(byRoleAttempt.get("evidence_investigator:1"), "evidence_investigator", 1, missing),
    roleRow(byRoleAttempt.get("regulatory_analyst:1"), "regulatory_analyst", 1, missing),
  ];
  const rows: AgentTimelineRow[] = [
    {
      id: "rules",
      label: "Rules",
      purpose: "Evaluate deterministic fraud and AML indicators.",
      status: deterministicStatus(state, "rules"),
    },
    {
      id: "risk",
      label: "Risk scored",
      purpose: "Score the transaction and record its model provenance.",
      status: deterministicStatus(state, "scoring"),
    },
    {
      id: "parallel-investigation",
      label: "Parallel investigation",
      purpose: "Investigate case evidence and regulation independently before synthesis.",
      status: groupStatus(parallel),
      children: parallel,
    },
  ];
  const attemptCount = Math.max(1, state.revisionCount + 1);
  for (let attempt = 1; attempt <= attemptCount; attempt += 1) {
    const writer = byRoleAttempt.get(`sar_writer:${attempt}`);
    rows.push(
      roleRow(writer, "sar_writer", attempt, missing),
      roleRow(
        byRoleAttempt.get(`compliance_reviewer:${attempt}`),
        "compliance_reviewer",
        attempt,
        writer?.status === "failed" ? "skipped" : missing,
      ),
    );
  }
  rows.push({
    id: "human-review",
    label: "Awaiting human review",
    purpose: "Keep the report in draft until an authorized reviewer decides.",
    status:
      state.status === "completed" && state.sarDraftId
        ? "awaiting"
        : state.status === "completed" || state.status === "failed"
          ? "skipped"
          : "pending",
  });
  return rows;
}
