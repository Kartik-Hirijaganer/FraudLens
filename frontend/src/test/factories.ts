// Test-only fixtures + a fake ApiClient. Lives under src/test/ so it is exempt from the
// SUMMARY-header rule and excluded from coverage; it exists purely to keep component/page
// tests terse (build a realistic object, override only the fields a test cares about).
import { vi } from "vitest";

import type {
  AlertDetailResponse,
  AlertView,
  ApiClient,
  DashboardMetrics,
  DeploymentResponse,
  DriftReportView,
  InvestigationSnapshot,
  ModelVersionResponse,
  SarDraftView,
  TransactionResponse,
  TrainingRunView,
} from "../lib/api";

export function transaction(overrides: Partial<TransactionResponse> = {}): TransactionResponse {
  return {
    transactionId: "tx-1",
    externalId: "ext-1",
    agencyId: "agency-1",
    amount: "12500.00",
    currency: "USD",
    occurredAt: "2026-06-10T09:00:00Z",
    originAccount: "****1234",
    destAccount: "****9876",
    channel: "wire",
    country: "US",
    riskBand: "high",
    latestRunId: null,
    ingestedAt: "2026-06-10T09:01:00Z",
    ...overrides,
  };
}

export function alertView(overrides: Partial<AlertView> = {}): AlertView {
  return {
    alertId: "alert-1",
    transactionId: "tx-1",
    runId: "run-1",
    status: "open",
    severity: "high",
    amount: "9500.00",
    currency: "USD",
    assignedTo: null,
    reviewFlags: [{ flag: "critical_band", reason: "Risk band is critical" }],
    createdAt: "2026-06-11T10:00:00Z",
    updatedAt: "2026-06-11T10:00:00Z",
    ...overrides,
  };
}

function sarDraft(overrides: Partial<SarDraftView> = {}): SarDraftView {
  return {
    sarDraftId: "sar-1",
    runId: "run-1",
    alertId: "alert-1",
    version: 1,
    status: "draft",
    content: "Suspicious structuring activity observed.",
    structured: {},
    citations: [
      { citation: "31 CFR 1020.320", title: "SAR filing", source: "FinCEN", snippet: "..." },
    ],
    modelId: "mock",
    promptVersion: "sar-v1",
    promptHash: "abc123",
    tokenUsage: {},
    costUsd: "0",
    createdAt: "2026-06-11T10:05:00Z",
    ...overrides,
  };
}

export function alertDetail(overrides: Partial<AlertDetailResponse> = {}): AlertDetailResponse {
  return {
    alert: alertView(),
    sarDraft: sarDraft(),
    actions: [],
    ...overrides,
  };
}

export function snapshot(overrides: Partial<InvestigationSnapshot> = {}): InvestigationSnapshot {
  return {
    runId: "run-1",
    transactionId: "tx-1",
    status: "completed",
    riskScore: 0.82,
    riskBand: "critical",
    fraudProbability: 0.91,
    modelVersion: "model-v1",
    rulesVersion: "rules-v1",
    ragVersion: "rag-v1",
    promptVersion: "sar-v1",
    errorCode: null,
    topFeatures: [{ feature: "amount", value: 12500, shapValue: 0.3 }],
    ruleHits: [
      { code: "STRUCTURING", ruleType: "structuring", severity: "high", reason: "near threshold" },
    ],
    citations: [
      { citation: "31 CFR 1020.320", title: "SAR filing", source: "FinCEN", snippet: "..." },
    ],
    sarStatus: "draft",
    sarDraftId: "sar-1",
    createdAt: "2026-06-11T10:00:00Z",
    updatedAt: "2026-06-11T10:05:00Z",
    ...overrides,
  };
}

export function modelVersion(overrides: Partial<ModelVersionResponse> = {}): ModelVersionResponse {
  return {
    versionId: "ver-1",
    versionLabel: "model-v1",
    status: "active",
    artifactUri: "models/model-v1",
    featureSpec: { features: ["amount"] },
    metrics: { prAuc: 0.84 },
    notes: "seed fixture model",
    createdAt: "2026-06-09T08:00:00Z",
    ...overrides,
  };
}

export function deployment(overrides: Partial<DeploymentResponse> = {}): DeploymentResponse {
  return {
    activeVersionLabel: "model-v1",
    canaryVersionLabel: null,
    canaryPercent: 0,
    previousActiveVersionLabel: null,
    updatedAt: "2026-06-09T08:00:00Z",
    ...overrides,
  };
}

function trainingRun(overrides: Partial<TrainingRunView> = {}): TrainingRunView {
  return {
    trainingRunId: "trun-1",
    trigger: "manual",
    status: "succeeded",
    datasetId: "ds-1",
    artifactUri: "models/model-v2",
    metrics: { prAuc: 0.86 },
    createdAt: "2026-06-12T08:00:00Z",
    ...overrides,
  };
}

export function driftReport(overrides: Partial<DriftReportView> = {}): DriftReportView {
  return {
    driftReportId: "drift-1",
    versionLabel: "model-v1",
    window: "30d",
    severity: "low",
    advisory: true,
    metrics: { psi: 0.07 },
    createdAt: "2026-06-12T08:00:00Z",
    ...overrides,
  };
}

export function dashboardMetrics(overrides: Partial<DashboardMetrics> = {}): DashboardMetrics {
  return {
    alerts: {
      open: 3,
      pendingReview: 1,
      inReview: 1,
      escalated: 2,
      resolved: 5,
      dismissed: 2,
      total: 14,
    },
    transactions: { total: 50, byRiskBand: { unscored: 44, high: 6 } },
    runs: { pending: 0, running: 1, completed: 12, failed: 1, total: 14 },
    sar: { draft: 4, reviewed: 1, approved: 3, rejected: 1, failed: 0, total: 9 },
    llmCost: { todayUsd: "0.120000", totalUsd: "1.450000", draftCount: 9 },
    modelHealth: {
      activeVersionLabel: "model-v1",
      canaryVersionLabel: null,
      canaryPercent: 0,
      recentInferenceCount: 14,
      latestDriftSeverity: "low",
    },
    ...overrides,
  };
}

export function makeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  const base: ApiClient = {
    health: vi.fn(() =>
      Promise.resolve({ status: "ok", service: "FraudLens", version: "0", environment: "dev" }),
    ),
    listTransactions: vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null }),
    ),
    getTransaction: vi.fn(() => Promise.resolve(transaction())),
    ingestTransaction: vi.fn(() => Promise.resolve(transaction())),
    ingestBatch: vi.fn(() =>
      Promise.resolve({
        accepted: 0,
        duplicates: 0,
        rejected: 0,
        dryRun: false,
        transactions: [],
        sampleErrors: [],
      }),
    ),
    uploadCsv: vi.fn(() =>
      Promise.resolve({
        jobId: "job-1",
        accepted: 1,
        duplicates: 0,
        rejected: 0,
        sampleErrors: [],
      }),
    ),
    startInvestigation: vi.fn(() => Promise.resolve({ runId: "run-1" })),
    getInvestigation: vi.fn(() => Promise.resolve(snapshot())),
    investigationStreamUrl: vi.fn((runId: string) => `/api/v1/investigations/${runId}/stream`),
    listAlerts: vi.fn(() => Promise.resolve({ alerts: [alertView()] })),
    getAlert: vi.fn(() => Promise.resolve(alertDetail())),
    actOnAlert: vi.fn(() => Promise.resolve(alertView())),
    reviewSar: vi.fn(() => Promise.resolve(sarDraft())),
    listModelVersions: vi.fn(() =>
      Promise.resolve({ versions: [modelVersion()], activeVersionLabel: "model-v1" }),
    ),
    getDeployment: vi.fn(() => Promise.resolve(deployment())),
    triggerTraining: vi.fn(() =>
      Promise.resolve({
        jobId: "job-1",
        trigger: "manual",
        status: "submitted",
        labelTotal: 40,
        labelPositives: 12,
        labelNegatives: 28,
      }),
    ),
    listTrainingRuns: vi.fn(() => Promise.resolve({ trainingRuns: [trainingRun()] })),
    promoteToShadow: vi.fn(() => Promise.resolve(modelVersion({ status: "shadow" }))),
    approveVersion: vi.fn(() => Promise.resolve(modelVersion({ status: "shadow" }))),
    setCanary: vi.fn(() => Promise.resolve(deployment())),
    rollbackDeployment: vi.fn(() =>
      Promise.resolve({ action: "restored_previous", deployment: deployment() }),
    ),
    evaluateCanary: vi.fn(() =>
      Promise.resolve({
        aborted: false,
        activeCount: 10,
        activeMean: 0.2,
        canaryCount: 10,
        canaryMean: 0.22,
        deviation: 0.02,
        deployment: deployment(),
      }),
    ),
    listDriftReports: vi.fn(() => Promise.resolve({ driftReports: [driftReport()] })),
    getDashboardMetrics: vi.fn(() => Promise.resolve(dashboardMetrics())),
  };
  return { ...base, ...overrides };
}
