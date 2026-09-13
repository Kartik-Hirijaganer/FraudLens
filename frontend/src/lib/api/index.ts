/**
 * Summary: Stable typed-client barrel and request implementation for the FraudLens gateway.
 *
 * Key classes:
 * - ApiClient:
 *
 * Key functions:
 * - fetchApiHealth: fetch the public API heartbeat.
 * - fetchCurrentUser: fetch the authenticated identity.
 * - fetchPortfolioDemoConfig: fetch the public synthetic demo projection.
 * - createApiClient: construct a client around an injectable fetch implementation.
 * - apiClient:
 *
 * Notes:
 * - Existing imports from lib/api resolve through this barrel unchanged.
 */
import { config } from "../config";
import { signOut, updateAccessToken, withSessionHeaders } from "../session";
import { refreshAccessToken } from "../supabase";
import type {
  AlertActionRequest,
  AlertDetailResponse,
  AlertListResponse,
  AlertView,
  ApiHealth,
  BatchIngestRequest,
  BatchIngestResponse,
  CanaryPercent,
  CsvUploadResponse,
  CurrentUser,
  InvestigationSnapshot,
  InvestigationStartResponse,
  ListAlertsParams,
  ListTransactionsParams,
  PortfolioDemoConfig,
  SarDraftView,
  SarReviewRequest,
  TransactionIngestRequest,
  TransactionListResponse,
  TransactionResponse,
} from "./alerts";
import { ApiError } from "./alerts";
import type {
  CanaryEvaluationResponse,
  DashboardMetrics,
  DeploymentResponse,
  DriftReportListResponse,
  ModelVersionListResponse,
  ModelVersionResponse,
  RollbackResponse,
  TrainingRunListResponse,
  TrainingRunTriggerResponse,
} from "./models";

export * from "./alerts";
export * from "./models";

export interface ApiClient {
  health(): Promise<ApiHealth>;
  me(): Promise<CurrentUser>;
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
  regenerateSar(runId: string): Promise<SarDraftView>;
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

export async function fetchCurrentUser(
  accessToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<CurrentUser> {
  const response = await fetchImpl(`${config.apiBaseUrl}/api/v1/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return (await response.json()) as CurrentUser;
}

export async function fetchPortfolioDemoConfig(
  fetchImpl: typeof fetch = fetch,
): Promise<PortfolioDemoConfig> {
  // Deliberately session-free: the login screen calls this before any session exists, so it
  // sends no auth headers and never triggers the 401 refresh/sign-out path in `send<T>()`.
  const response = await fetchImpl(`${config.apiBaseUrl}/api/v1/portfolio-demo/config`);
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return (await response.json()) as PortfolioDemoConfig;
}

export function createApiClient(fetchImpl: typeof fetch = fetch): ApiClient {
  async function send<T>(path: string, init?: RequestInit): Promise<T> {
    let response = await fetchImpl(`${config.apiBaseUrl}${path}`, withSessionHeaders(init));
    if (response.status === 401) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        updateAccessToken(refreshed);
        response = await fetchImpl(`${config.apiBaseUrl}${path}`, withSessionHeaders(init));
      }
      if (response.status === 401) {
        signOut();
      }
    }
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
    me: () => send<CurrentUser>("/api/v1/me"),
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
    regenerateSar: (runId) =>
      send<SarDraftView>(`/api/v1/investigations/${runId}/sar/regenerate`, jsonInit("POST")),
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
