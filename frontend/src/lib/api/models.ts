/**
 * Summary: Model lifecycle, drift, and dashboard API contracts.
 *
 * Key classes:
 * - ModelVersionResponse:
 * - ModelVersionListResponse:
 * - DeploymentResponse:
 * - RollbackResponse:
 * - CanaryEvaluationResponse:
 * - TrainingRunView:
 * - TrainingRunListResponse:
 * - TrainingRunTriggerResponse:
 * - DriftReportView:
 * - DriftReportListResponse:
 * - AlertMetrics:
 * - TransactionMetrics:
 * - RunMetrics:
 * - SarMetrics:
 * - LlmCostMetrics:
 * - ModelHealthMetrics:
 * - DashboardMetrics:
 *
 * Key functions:
 * - (none)
 *
 * Notes:
 * - Types are data-only and preserve the existing API barrel surface.
 */
import type { ModelVersionStatus, Severity } from "./alerts";

export interface ModelVersionResponse {
  versionId: string;
  versionLabel: string;
  status: ModelVersionStatus;
  artifactUri: string;
  featureSpec: Record<string, unknown>;
  metrics: Record<string, unknown>;
  notes: string;
  createdAt: string;
}

export interface ModelVersionListResponse {
  versions: ModelVersionResponse[];
  activeVersionLabel: string | null;
}

export interface DeploymentResponse {
  activeVersionLabel: string;
  canaryVersionLabel: string | null;
  canaryPercent: number;
  previousActiveVersionLabel: string | null;
  updatedAt: string;
}

export interface RollbackResponse {
  action: string;
  deployment: DeploymentResponse;
}

export interface CanaryEvaluationResponse {
  aborted: boolean;
  activeCount: number;
  activeMean: number;
  canaryCount: number;
  canaryMean: number;
  deviation: number;
  deployment: DeploymentResponse;
}

export interface TrainingRunView {
  trainingRunId: string;
  trigger: string;
  status: string;
  datasetId: string;
  artifactUri: string | null;
  metrics: Record<string, unknown>;
  createdAt: string;
}

export interface TrainingRunListResponse {
  trainingRuns: TrainingRunView[];
}

export interface TrainingRunTriggerResponse {
  jobId: string;
  trigger: string;
  status: string;
  labelTotal: number;
  labelPositives: number;
  labelNegatives: number;
}

export interface DriftReportView {
  driftReportId: string;
  versionLabel: string;
  window: string;
  severity: Severity;
  advisory: boolean;
  metrics: Record<string, unknown>;
  createdAt: string;
}

export interface DriftReportListResponse {
  driftReports: DriftReportView[];
}

export interface AlertMetrics {
  open: number;
  pendingReview: number;
  inReview: number;
  escalated: number;
  resolved: number;
  dismissed: number;
  total: number;
}

export interface TransactionMetrics {
  total: number;
  byRiskBand: Record<string, number>;
}

export interface RunMetrics {
  pending: number;
  running: number;
  completed: number;
  failed: number;
  total: number;
}

export interface SarMetrics {
  draft: number;
  reviewed: number;
  approved: number;
  rejected: number;
  failed: number;
  total: number;
}

export interface LlmCostMetrics {
  todayUsd: string;
  totalUsd: string;
  draftCount: number;
}

export interface ModelHealthMetrics {
  activeVersionLabel: string | null;
  canaryVersionLabel: string | null;
  canaryPercent: number;
  recentInferenceCount: number;
  latestDriftSeverity: string | null;
}

export interface DashboardMetrics {
  alerts: AlertMetrics;
  transactions: TransactionMetrics;
  runs: RunMetrics;
  sar: SarMetrics;
  llmCost: LlmCostMetrics;
  modelHealth: ModelHealthMetrics;
}
