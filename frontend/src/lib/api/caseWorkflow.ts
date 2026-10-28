/**
 * Summary: Typed maker-checker commands, frozen rounds, and backend action capabilities.
 *
 * Key classes:
 * - ActionCapability: server-owned eligibility and explanation.
 * - WorkingSarRequest: versioned narrative save.
 * - SubmissionRequest: immutable maker recommendation.
 * - DecisionRequest: independent checker decision.
 * - WorkflowCommandResult: durable command receipt.
 * - SubmissionView: frozen round with decision and PDF readiness.
 *
 * Key functions:
 * - CASE_QUEUES:
 *
 * Notes:
 * - Person identifiers are read-only response data, never mutation inputs.
 */
import type { SarDraftView, TrainingLabel } from "./alerts";

export interface ActionCapability {
  allowed: boolean;
  reason: string | null;
}
export type CaseAction =
  | "pickup"
  | "edit"
  | "regenerate"
  | "submit"
  | "dismiss"
  | "comment"
  | "approve"
  | "return"
  | "decline";
export const CASE_QUEUES = ["available", "mine", "awaiting_review", "returned", "closed"] as const;
export type CaseQueue = (typeof CASE_QUEUES)[number];
export interface WorkingSarRequest {
  expectedCaseVersion: number;
  expectedSarVersion: number | null;
  content: string;
}
export interface SubmissionRequest {
  expectedCaseVersion: number;
  recommendation: "sar" | "no_sar";
  rationale: string;
  sarVersion?: number;
}
export interface DecisionRequest {
  expectedCaseVersion: number;
  decision: "approve" | "return" | "decline";
  reason: string;
  outcome?: TrainingLabel;
}
export interface WorkflowCommandResult {
  alertId: string;
  submissionId: string;
  caseVersion: number;
  status: string;
  decision: string | null;
}
export interface SubmissionView {
  submissionId: string;
  round: number;
  makerAccountId: string;
  submittedByAccountId?: string | null;
  canRetryPdf: boolean;
  makerPersonId: string;
  contributorPersonIds: string[];
  recommendation: string;
  rationale: string | null;
  sarDraft: SarDraftView | null;
  evidence: Record<string, unknown>;
  decision: string | null;
  reason: string | null;
  outcome: string | null;
  checkerAccountId: string | null;
  checkerPersonId: string | null;
  pdfStatus: "unavailable" | "pending" | "ready" | "failed";
  createdAt: string;
}
