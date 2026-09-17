/**
 * Summary: Transaction, investigation, alert, and SAR API contracts.
 *
 * Key classes:
 * - ApiError: stable non-success gateway error.
 * - ApiHealth:
 * - CurrentUser:
 * - PortfolioDemoAgencyConfig:
 * - PortfolioDemoPersonaConfig:
 * - PortfolioDemoConfig:
 * - TransactionResponse:
 * - ListTransactionsParams:
 * - TransactionListResponse:
 * - TransactionIngestRequest:
 * - BatchIngestRequest:
 * - BatchIngestResponse:
 * - CsvUploadResponse:
 * - InvestigationStartResponse:
 * - InvestigationSnapshot:
 * - AlertView:
 * - ListAlertsParams:
 * - AlertListResponse:
 * - AlertActionView:
 * - SarDraftView:
 * - SarModelInputView:
 * - AlertDetailResponse:
 * - AlertActionRequest:
 * - SarReviewRequest:
 *
 * Key functions:
 * - STATUS_LABELS:
 * - statusLabel: render a lifecycle status for display.
 *
 * Notes:
 * - Types mirror the camelCase API boundary.
 */
import { humanize } from "../format";
import type { RoleAccent, UserRole } from "../session";
import type {
  AgentRun,
  InvestigationSnapshotData,
  RegulationCitation,
  WorkflowMode,
} from "../investigation";

export type Severity = "low" | "medium" | "high" | "critical";
export type AlertOrigin = "pipeline" | "seed";
export type AlertStatus =
  | "open"
  | "pending_review"
  | "in_review"
  | "escalated"
  | "resolved"
  | "dismissed";
type AlertActionType = "assign" | "comment" | "escalate" | "resolve" | "dismiss";
export type TrainingLabel = "confirmed_fraud" | "false_positive" | "false_negative" | "benign";
export type SarStatus = "draft" | "reviewed" | "approved" | "rejected" | "failed";
type SarReviewDecision = "approve" | "reject" | "edit";
export type ModelVersionStatus =
  | "candidate"
  | "shadow"
  | "canary"
  | "active"
  | "archived"
  | "rejected";
export type CanaryPercent = 5 | 25 | 50 | 100;

export const STATUS_LABELS: Record<AlertStatus, string> = {
  open: humanize("open"),
  pending_review: humanize("pending_review"),
  in_review: humanize("in_review"),
  escalated: humanize("escalated"),
  resolved: "Completed",
  dismissed: "Archived",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as AlertStatus] ?? humanize(status);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
}

export interface CurrentUser {
  email: string;
  displayName: string;
  role: UserRole;
  agencyId: string;
}

// The backend's safe public projection of `config/portfolio-demo.yaml`. Persona presentation
// data is fetched, never declared in TypeScript, so the frontend holds no demo identity.
export interface PortfolioDemoAgencyConfig {
  id: string;
  name: string;
  slug: string;
  researchPartitionKey: string;
}

export interface PortfolioDemoPersonaConfig {
  key: string;
  role: UserRole;
  email: string;
  displayName: string;
  initials: string;
  pickerName: string;
  pickerTag: string;
  pickerAccent: RoleAccent;
}

export interface PortfolioDemoConfig {
  storyVersion: string;
  agency: PortfolioDemoAgencyConfig;
  personas: PortfolioDemoPersonaConfig[];
  syntheticPassword: string;
}

export interface TransactionResponse {
  transactionId: string;
  externalId: string;
  agencyId: string;
  amount: string;
  currency: string;
  occurredAt: string;
  originAccount: string;
  destAccount: string;
  channel: string;
  country: string;
  riskBand: string | null;
  latestRunId: string | null;
  ingestedAt: string;
}

export interface ListTransactionsParams {
  limit?: number;
  cursor?: string;
  riskBand?: string;
  search?: string;
}

export interface TransactionListResponse {
  transactions: TransactionResponse[];
  nextCursor: string | null;
  total: number;
}

export interface TransactionIngestRequest {
  externalId: string;
  amount: string;
  currency: string;
  occurredAt: string;
  originAccount: string;
  destAccount: string;
  channel: string;
  country: string;
  features?: Record<string, unknown>;
}

export interface BatchIngestRequest {
  transactions: TransactionIngestRequest[];
  dryRun?: boolean;
}

interface IngestRejection {
  index: number;
  externalId: string | null;
  code: string;
  message: string;
}

export interface BatchIngestResponse {
  accepted: number;
  duplicates: number;
  rejected: number;
  dryRun: boolean;
  transactions: TransactionResponse[];
  sampleErrors: IngestRejection[];
}

export interface CsvUploadResponse {
  jobId: string;
  accepted: number;
  duplicates: number;
  rejected: number;
  sampleErrors: IngestRejection[];
}

export interface InvestigationStartResponse {
  runId: string;
}

export interface InvestigationSnapshot extends InvestigationSnapshotData {
  runId: string;
  promptVersion: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AlertView {
  alertId: string;
  transactionId: string;
  runId: string;
  origin: AlertOrigin;
  status: AlertStatus;
  severity: Severity;
  amount: string;
  currency: string;
  assignedTo: string | null;
  assignedToName: string | null;
  reviewFlags: { flag: string; reason: string }[];
  createdAt: string;
  updatedAt: string;
}

export interface ListAlertsParams {
  limit?: number;
  offset?: number;
  status?: AlertStatus;
}

export interface AlertListResponse {
  alerts: AlertView[];
}

export interface AlertActionView {
  actionId: string;
  action: AlertActionType;
  actorId: string;
  note: string | null;
  fromStatus: AlertStatus | null;
  toStatus: AlertStatus | null;
  createdAt: string;
}

export interface SarDraftView {
  sarDraftId: string;
  runId: string;
  alertId: string | null;
  version: number;
  status: SarStatus;
  qualityStatus: "not_run" | "passed" | "failed";
  quality: Record<string, unknown>;
  modelInput: SarModelInputView | null;
  content: string;
  structured: Record<string, unknown>;
  citations: RegulationCitation[];
  modelId: string;
  promptVersion: string;
  promptHash: string;
  workflow: WorkflowMode;
  revisionCount: number;
  tokenUsage: Record<string, unknown>;
  costUsd: string;
  createdAt: string;
}

export interface SarModelInputView {
  caseAlias: string;
  subjectAlias: string;
  counterpartyAlias: string;
  transaction: Record<string, unknown>;
  aggregates: Array<Record<string, unknown>>;
  riskBand: string;
  fraudProbability: number;
  ruleHits: Array<Record<string, unknown>>;
  shapDrivers: Array<Record<string, unknown>>;
  regulations: Array<{ citationId?: string } & Record<string, unknown>>;
  unknowns: string[];
}

export interface AlertDetailResponse {
  alert: AlertView;
  sarDraft: SarDraftView | null;
  actions: AlertActionView[];
  agentExecutions: AgentRun[];
  workflowMode: WorkflowMode;
  graphVersion: string | null;
  revisionCount: number;
  sarContent: string | null;
}

export interface AlertActionRequest {
  action: AlertActionType;
  assigneeId?: string;
  note?: string;
  label?: TrainingLabel;
}

export interface SarReviewRequest {
  decision: SarReviewDecision;
  editedContent?: string;
  reason?: string;
}
