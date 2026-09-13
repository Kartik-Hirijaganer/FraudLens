/**
 * Summary: PHI-free investigation state, event, step, and timeline contracts.
 *
 * Key classes:
 * - AgentToolCall:
 * - AgentRun:
 * - AgentTimelineRow:
 * - ShapFeature:
 * - RegulationCitation:
 * - InvestigationRuleHit:
 * - InvestigationState:
 * - InvestigationSnapshotData:
 *
 * Key functions:
 * - INVESTIGATION_EVENTS:
 * - CASE_STEPS:
 * - NO_ALERT_CASE_STEPS:
 *
 * Notes:
 * - Constants are the canonical SSE event and analyst-step ordering.
 */
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
