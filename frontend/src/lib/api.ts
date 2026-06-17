/**
 * Summary: The typed client for the FraudLens gateway API (plan §5, §16 Phase 11). The
 * surface is camelCase (FraudLens casing), so the response interfaces use camelCase
 * fields directly and need no remapping. `createApiClient` captures an injectable
 * `fetch` (so tests never touch the network) and exposes one method per consumed
 * endpoint across P3–P10 — transactions, investigations (create/snapshot/stream URL),
 * alerts + SAR review, and the admin model lifecycle. A non-2xx response is parsed into
 * an `ApiError` carrying the stable envelope `code` (which `lib/errors.ts` maps to UX);
 * the error message is the envelope's PHI-free message, never a raw body or stack.
 *
 * Key classes:
 * - ApiError: thrown on a non-2xx response (carries status + envelope code + requestId).
 * - ApiHealth: shape of GET /api/v1/health.
 * - TransactionResponse: a persisted transaction (masked accounts).
 * - ListTransactionsParams: query params for the transactions list (limit/cursor/riskBand).
 * - TransactionListResponse: a page of transactions + nextCursor.
 * - TransactionIngestRequest: a single transaction-ingest body.
 * - BatchIngestRequest: a batch-ingest body (+ dryRun).
 * - BatchIngestResponse: batch-ingest outcome (counts + rows + sample errors).
 * - CsvUploadResponse: CSV-upload outcome (jobId + counts + sample errors).
 * - InvestigationStartResponse: the 202 acknowledgement (runId).
 * - InvestigationSnapshot: the authoritative run snapshot.
 * - AlertView: an alert summary projection.
 * - ListAlertsParams: query params for the alerts list (limit/offset/status).
 * - AlertListResponse: a page of alerts.
 * - AlertActionView: one append-only triage action.
 * - SarDraftView: a persisted SAR draft projection.
 * - AlertDetailResponse: an alert + its SAR draft + action history.
 * - AlertActionRequest: the triage-action body.
 * - SarReviewRequest: the SAR review body (approve/reject/edit).
 * - ModelVersionResponse: one registry model version.
 * - ModelVersionListResponse: the registry versions + active label.
 * - DeploymentResponse: the live active/canary deployment pointer.
 * - RollbackResponse: what a rollback did + the resulting pointer.
 * - CanaryEvaluationResponse: the canary auto-abort verdict + arm stats.
 * - TrainingRunView: one model training run.
 * - TrainingRunListResponse: the training-run history.
 * - TrainingRunTriggerResponse: the 202 retrain acknowledgement + label counts.
 * - DriftReportView: one advisory drift report.
 * - DriftReportListResponse: advisory drift reports.
 * - AlertMetrics: alert counts by status (open/in-review/resolved/dismissed + total).
 * - TransactionMetrics: total transactions + a count per risk band.
 * - RunMetrics: investigation-run counts by status + total.
 * - SarMetrics: SAR-draft counts by review status + total.
 * - LlmCostMetrics: SAR LLM spend (today + all-time USD) + drafted-SAR count.
 * - ModelHealthMetrics: active/canary labels + percent, inference count, latest drift.
 * - DashboardMetrics: the tenant-scoped dashboard aggregate (counts + LLM cost + model health).
 * - ApiClient: the interface of all endpoint methods (injected into pages for testing).
 *
 * Key functions:
 * - fetchApiHealth: standalone GET /api/v1/health (the walking-skeleton heartbeat).
 * - createApiClient: build an ApiClient bound to an injectable fetch.
 * - apiClient: the default ApiClient for app use.
 *
 * Notes:
 * - The base URL comes from config (VITE_*), never a hardcoded host; the SSE stream URL
 * is built here too so every path lives in one place (rule 5).
 */
import { config } from "./config";
import type { InvestigationRuleHit, RegulationCitation, ShapFeature } from "./investigation";

export type Severity = "low" | "medium" | "high" | "critical";
export type AlertStatus = "open" | "in_review" | "resolved" | "dismissed";
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
}

export interface TransactionListResponse {
  transactions: TransactionResponse[];
  nextCursor: string | null;
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

export interface InvestigationSnapshot {
  runId: string;
  transactionId: string;
  status: string;
  riskScore: number | null;
  riskBand: string | null;
  fraudProbability: number | null;
  modelVersion: string | null;
  rulesVersion: string | null;
  ragVersion: string | null;
  promptVersion: string | null;
  errorCode: string | null;
  topFeatures: ShapFeature[];
  ruleHits: InvestigationRuleHit[];
  citations: RegulationCitation[];
  sarStatus: string | null;
  sarDraftId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AlertView {
  alertId: string;
  transactionId: string;
  runId: string;
  status: AlertStatus;
  severity: Severity;
  assignedTo: string | null;
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
  fromStatus: string | null;
  toStatus: string | null;
  createdAt: string;
}

export interface SarDraftView {
  sarDraftId: string;
  runId: string;
  alertId: string | null;
  version: number;
  status: SarStatus;
  content: string;
  structured: Record<string, unknown>;
  citations: RegulationCitation[];
  modelId: string;
  promptVersion: string;
  promptHash: string;
  tokenUsage: Record<string, unknown>;
  costUsd: string;
  createdAt: string;
}

export interface AlertDetailResponse {
  alert: AlertView;
  sarDraft: SarDraftView | null;
  actions: AlertActionView[];
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
  inReview: number;
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

export interface ApiClient {
  health(): Promise<ApiHealth>;
  listTransactions(params?: ListTransactionsParams): Promise<TransactionListResponse>;
  getTransaction(transactionId: string): Promise<TransactionResponse>;
  ingestTransaction(body: TransactionIngestRequest): Promise<TransactionResponse>;
  ingestBatch(body: BatchIngestRequest): Promise<BatchIngestResponse>;
  uploadCsv(csvText: string): Promise<CsvUploadResponse>;
  startInvestigation(
    body: { transactionId: string; modelOverride?: string },
    idempotencyKey?: string,
  ): Promise<InvestigationStartResponse>;
  getInvestigation(runId: string): Promise<InvestigationSnapshot>;
  investigationStreamUrl(runId: string): string;
  listAlerts(params?: ListAlertsParams): Promise<AlertListResponse>;
  getAlert(alertId: string): Promise<AlertDetailResponse>;
  actOnAlert(alertId: string, body: AlertActionRequest): Promise<AlertView>;
  reviewSar(alertId: string, body: SarReviewRequest): Promise<SarDraftView>;
  listModelVersions(): Promise<ModelVersionListResponse>;
  getDeployment(): Promise<DeploymentResponse>;
  triggerTraining(trigger?: "manual" | "scheduled"): Promise<TrainingRunTriggerResponse>;
  listTrainingRuns(): Promise<TrainingRunListResponse>;
  promoteToShadow(versionId: string): Promise<ModelVersionResponse>;
  approveVersion(versionId: string): Promise<ModelVersionResponse>;
  setCanary(versionId: string, percent: CanaryPercent): Promise<DeploymentResponse>;
  rollbackDeployment(): Promise<RollbackResponse>;
  evaluateCanary(): Promise<CanaryEvaluationResponse>;
  listDriftReports(): Promise<DriftReportListResponse>;
  getDashboardMetrics(): Promise<DashboardMetrics>;
}

interface ErrorEnvelope {
  code: string;
  message: string;
  requestId: string;
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = `request failed with status ${response.status}`;
  let requestId: string | undefined;
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>;
    if (typeof body.code === "string") {
      code = body.code;
    }
    if (typeof body.message === "string") {
      message = body.message;
    }
    if (typeof body.requestId === "string") {
      requestId = body.requestId;
    }
  } catch {
    // Non-JSON error body — keep the status-derived code/message.
  }
  return new ApiError(response.status, code, message, requestId);
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

export async function fetchApiHealth(fetchImpl: typeof fetch = fetch): Promise<ApiHealth> {
  const response = await fetchImpl(`${config.apiBaseUrl}/api/v1/health`);
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return (await response.json()) as ApiHealth;
}

export function createApiClient(fetchImpl: typeof fetch = fetch): ApiClient {
  async function send<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetchImpl(`${config.apiBaseUrl}${path}`, init);
    if (!response.ok) {
      throw await errorFromResponse(response);
    }
    return (await response.json()) as T;
  }

  function jsonInit(method: string, body?: unknown, headers?: Record<string, string>): RequestInit {
    return {
      method,
      headers: { "Content-Type": "application/json", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    };
  }

  return {
    health: () => send<ApiHealth>("/api/v1/health"),
    listTransactions: (params = {}) =>
      send<TransactionListResponse>(`/api/v1/transactions${query({ ...params })}`),
    getTransaction: (transactionId) =>
      send<TransactionResponse>(`/api/v1/transactions/${transactionId}`),
    ingestTransaction: (body) =>
      send<TransactionResponse>("/api/v1/transactions", jsonInit("POST", body)),
    ingestBatch: (body) =>
      send<BatchIngestResponse>("/api/v1/transactions/batch", jsonInit("POST", body)),
    uploadCsv: (csvText) =>
      send<CsvUploadResponse>("/api/v1/transactions/upload", {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body: csvText,
      }),
    startInvestigation: (body, idempotencyKey) =>
      send<InvestigationStartResponse>(
        "/api/v1/investigations",
        jsonInit("POST", body, idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined),
      ),
    getInvestigation: (runId) => send<InvestigationSnapshot>(`/api/v1/investigations/${runId}`),
    investigationStreamUrl: (runId) => `${config.apiBaseUrl}/api/v1/investigations/${runId}/stream`,
    listAlerts: (params = {}) => send<AlertListResponse>(`/api/v1/alerts${query({ ...params })}`),
    getAlert: (alertId) => send<AlertDetailResponse>(`/api/v1/alerts/${alertId}`),
    actOnAlert: (alertId, body) =>
      send<AlertView>(`/api/v1/alerts/${alertId}/actions`, jsonInit("POST", body)),
    reviewSar: (alertId, body) =>
      send<SarDraftView>(`/api/v1/alerts/${alertId}/sar/review`, jsonInit("POST", body)),
    listModelVersions: () => send<ModelVersionListResponse>("/api/v1/model-versions"),
    getDeployment: () => send<DeploymentResponse>("/api/v1/model-deployment"),
    triggerTraining: (trigger = "manual") =>
      send<TrainingRunTriggerResponse>("/api/v1/training-runs", jsonInit("POST", { trigger })),
    listTrainingRuns: () => send<TrainingRunListResponse>("/api/v1/training-runs"),
    promoteToShadow: (versionId) =>
      send<ModelVersionResponse>(`/api/v1/model-versions/${versionId}/shadow`, jsonInit("POST")),
    approveVersion: (versionId) =>
      send<ModelVersionResponse>(`/api/v1/model-versions/${versionId}/approve`, jsonInit("POST")),
    setCanary: (versionId, percent) =>
      send<DeploymentResponse>(
        `/api/v1/model-versions/${versionId}/canary`,
        jsonInit("POST", { percent }),
      ),
    rollbackDeployment: () =>
      send<RollbackResponse>("/api/v1/model-deployment/rollback", jsonInit("POST")),
    evaluateCanary: () =>
      send<CanaryEvaluationResponse>("/api/v1/model-deployment/canary/evaluate", jsonInit("POST")),
    listDriftReports: () => send<DriftReportListResponse>("/api/v1/drift-reports"),
    getDashboardMetrics: () => send<DashboardMetrics>("/api/v1/dashboard/metrics"),
  };
}

export const apiClient: ApiClient = createApiClient();
