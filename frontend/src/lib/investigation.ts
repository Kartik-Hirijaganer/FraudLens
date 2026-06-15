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
 * - ShapFeature: one SHAP driver (feature name + value + signed contribution).
 * - RegulationCitation: one grounded regulatory citation (id + title + source + snippet).
 * - InvestigationRuleHit: one fired deterministic rule (code + type + severity + reason).
 * - InvestigationState: the accumulated UI state of a run.
 *
 * Key functions:
 * - initialInvestigationState: the empty starting state for a run.
 * - reduceInvestigation: fold one SSE message into the next state (pure).
 * - INVESTIGATION_EVENTS: the named server-sent events to subscribe to.
 * - INVESTIGATION_STEPS: the ordered pipeline steps (key + label) for the progress UI.
 *
 * Notes:
 * - Every payload field is read defensively from `unknown` (a malformed frame degrades to
 *   safe defaults), so a bad event can never throw inside the live render path.
 */
import type { SseMessage } from "./sse";

export type InvestigationStatus = "starting" | "running" | "completed" | "failed";

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
  errorCode?: string;
  lastEventId: string;
}

export const INVESTIGATION_EVENTS = [
  "run.started",
  "step.rules.completed",
  "step.scoring.completed",
  "step.shap.completed",
  "step.rag.completed",
  "sar.started",
  "sar.token",
  "run.completed",
  "run.failed",
] as const;

export const INVESTIGATION_STEPS = [
  { key: "rules", label: "Rules" },
  { key: "scoring", label: "Scoring" },
  { key: "shap", label: "Explain (SHAP)" },
  { key: "rag", label: "Regulations" },
  { key: "sar", label: "SAR draft" },
] as const;

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
    case "run.completed":
      return {
        ...base,
        status: "completed",
        riskScore: numberOf(data.riskScore),
        riskBand: stringOf(data.riskBand),
        modelVersion: stringOf(data.modelVersion) ?? state.modelVersion,
        sarDraftId: stringOf(data.sarDraftId),
        completedSteps: withStep(state.completedSteps, "sar"),
      };
    case "run.failed":
      return { ...base, status: "failed", errorCode: stringOf(data.code) };
    default:
      return base;
  }
}
