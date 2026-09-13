// Test-only complete fake ApiClient with injectable method overrides.
import { vi } from "vitest";
import type { ApiClient } from "../../lib/api";
import {
  alertDetail,
  alertView,
  dashboardMetrics,
  deployment,
  driftReport,
  modelVersion,
  sarDraft,
  snapshot,
  trainingRun,
  transaction,
} from "./domain";
import { TEST_EMAIL_DOMAIN } from "./session";

export function makeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  const base: ApiClient = {
    health: vi.fn(() =>
      Promise.resolve({ status: "ok", service: "FraudLens", version: "0", environment: "dev" }),
    ),
    me: vi.fn(() =>
      Promise.resolve({
        email: `analyst@${TEST_EMAIL_DOMAIN}`,
        displayName: "Test Analyst",
        role: "analyst" as const,
        agencyId: "agency-1",
      }),
    ),
    listTransactions: vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null, total: 1 }),
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
    regenerateSar: vi.fn(() => Promise.resolve(sarDraft({ version: 2 }))),
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
