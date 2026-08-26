// Test-only fixtures + a fake ApiClient. Lives under src/test/ so it is exempt from the
// SUMMARY-header rule and excluded from coverage; it exists purely to keep component/page
// tests terse (build a realistic object, override only the fields a test cares about).
import { vi } from "vitest";

import type { DemoRole, UserRole } from "../lib/session";
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
import type { AgentRun } from "../lib/investigation";
import { SAR_EVAL_TYPOLOGIES, SAR_EVAL_VARIANTS, type SarEvalStudyData } from "../lib/sarEvalStudy";

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
    origin: "pipeline",
    status: "open",
    severity: "high",
    amount: "9500.00",
    currency: "USD",
    assignedTo: null,
    assignedToName: null,
    reviewFlags: [{ flag: "critical_band", reason: "Risk band is critical" }],
    createdAt: "2026-06-11T10:00:00Z",
    updatedAt: "2026-06-11T10:00:00Z",
    ...overrides,
  };
}

export function sarDraft(overrides: Partial<SarDraftView> = {}): SarDraftView {
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
    workflow: "single_writer",
    revisionCount: 0,
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
    agentExecutions: [],
    workflowMode: "single_writer",
    graphVersion: null,
    revisionCount: 0,
    sarContent: "Suspicious structuring activity observed.",
    ...overrides,
  };
}

export function agentRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    agentRunId: "agent-run-1",
    agent: "evidence_investigator",
    attempt: 1,
    status: "completed",
    errorCode: null,
    modelId: "mock",
    promptVersion: "v1",
    promptHash: "prompt-hash",
    inputHash: "input-hash",
    resultHash: "result-hash",
    latencyMs: 25,
    modelCallCount: 1,
    inputTokens: 10,
    outputTokens: 20,
    totalTokens: 30,
    costUsd: "0",
    result: { summary: "Synthetic evidence reviewed." },
    toolCalls: [{ callId: "call-1", name: "rule_hits", status: "completed" }],
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
    sarContent: "Suspicious structuring activity observed.",
    workflowMode: "single_writer",
    graphVersion: null,
    revisionCount: 0,
    agentExecutions: [],
    alertId: "alert-1",
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

// Synthetic login personas standing in for the backend's public portfolio-demo projection.
// Test-only: production code receives these from the API, never from a TypeScript constant.
const PERSONA_PRESETS: Record<UserRole, Pick<DemoRole, "name" | "tag" | "accent" | "analyst">> = {
  analyst: {
    name: "Fraud Analyst",
    tag: "Queue",
    accent: "green",
    analyst: { name: "Alex Rivera", initials: "AR" },
  },
  reviewer: {
    name: "Reviewer",
    tag: "Approve",
    accent: "cyan",
    analyst: { name: "Morgan Diaz", initials: "MD" },
  },
  admin: {
    name: "Compliance Admin",
    tag: "Model",
    accent: "amber",
    analyst: { name: "Priya Shah", initials: "PS" },
  },
  auditor: {
    name: "Auditor",
    tag: "Read-only",
    accent: "slate",
    analyst: { name: "Jordan Lee", initials: "JL" },
  },
};

export const TEST_DEMO_AGENCY_ID = "00000000-0000-4000-8000-00000000d3m0";
// Test-owned mail domain. Deliberately NOT the configured personas' domain: the frontend
// suite must prove the picker renders whatever the backend projection returns, so it can
// never assert against a value copied out of config/portfolio-demo.yaml.
export const TEST_EMAIL_DOMAIN = "tenant.test";
const TEST_DEMO_PASSWORD = "synthetic-test-password";

export function demoPersona(role: UserRole, overrides: Partial<DemoRole> = {}): DemoRole {
  return {
    id: role,
    role,
    email: `${role}@${TEST_EMAIL_DOMAIN}`,
    demoPassword: TEST_DEMO_PASSWORD,
    agencyId: TEST_DEMO_AGENCY_ID,
    ...PERSONA_PRESETS[role],
    ...overrides,
  };
}

export function demoPersonas(): readonly DemoRole[] {
  return (["analyst", "reviewer", "admin", "auditor"] as const).map((role) => demoPersona(role));
}

export function sarEvalStudy(): SarEvalStudyData {
  const scenarioArm = {
    completenessPassed: 4,
    unsupportedClaimCount: 1,
    citationPrecision: 0.9,
    citationRecall: 0.8,
    fabricatedCitationCount: 0,
    costUsd: 0.02,
    latencyMs: 2_000,
    modelCalls: 1,
    elementAgreement: 0.8,
    unsupportedClaimCountAgreement: 0.8,
    unsupportedClaimSpanAgreement: 0.8,
    agreement: 0.8,
  };
  const scenarios = SAR_EVAL_TYPOLOGIES.map((typology) =>
    SAR_EVAL_VARIANTS.map((variant) => ({
      scenarioId: `${typology}-${variant}`,
      typology,
      variant,
      singleWriter: { ...scenarioArm },
      multiAgent: {
        ...scenarioArm,
        completenessPassed: 5,
        unsupportedClaimCount: 0,
        costUsd: 0.05,
        latencyMs: 3_200,
        modelCalls: 4,
        elementAgreement: 0.9,
        unsupportedClaimCountAgreement: 0.9,
        unsupportedClaimSpanAgreement: 0.9,
        agreement: 0.9,
      },
    })),
  ).flat();

  return {
    reportSha256: "a".repeat(64),
    runId: "sar-eval-test-run",
    seed: 17,
    syntheticData: true,
    scenarioCount: 32,
    bootstrapResamples: 10_000,
    judge: {
      modelId: "openrouter/anthropic/claude-opus-4.6",
      modelFamily: "anthropic",
      promptVersion: "v1",
      promptHash: "b".repeat(64),
      samplesPerNarrative: 3,
      blind: true,
      orderRandomized: true,
    },
    armProvenance: [
      {
        arm: "single_writer",
        writerModelId: "openrouter/openai/gpt-5-mini",
        writerModelFamily: "openai",
        modelIds: ["openrouter/openai/gpt-5-mini"],
        promptVersions: ["v1"],
        promptHashes: ["c".repeat(64)],
        graphVersion: null,
      },
      {
        arm: "multi_agent",
        writerModelId: "openrouter/openai/gpt-5-mini",
        writerModelFamily: "openai",
        modelIds: [
          "openrouter/x-ai/grok-4.3",
          "openrouter/google/gemini-2.5-flash",
          "openrouter/openai/gpt-5-mini",
          "openrouter/anthropic/claude-sonnet-4.6",
        ],
        promptVersions: ["evidence-v1", "regulatory-v1", "writer-v1", "reviewer-v1"],
        promptHashes: ["d".repeat(64), "e".repeat(64), "f".repeat(64), "1".repeat(64)],
        graphVersion: "agents-v1",
      },
    ],
    summary: {
      arms: [
        {
          arm: "single_writer",
          completenessRate: 0.7,
          unsupportedClaims: 0.5,
          citationPrecision: 0.88,
          citationRecall: 0.82,
          fabricatedCitationCount: 0.1,
          costUsd: 0.02,
          latencyMs: 2_000,
          modelCalls: 1,
          elementAgreement: 0.8,
          unsupportedClaimCountAgreement: 0.8,
          unsupportedClaimSpanAgreement: 0.8,
          agreement: 0.8,
        },
        {
          arm: "multi_agent",
          completenessRate: 0.8,
          unsupportedClaims: 0.25,
          citationPrecision: 0.9,
          citationRecall: 0.85,
          fabricatedCitationCount: 0,
          costUsd: 0.05,
          latencyMs: 3_200,
          modelCalls: 4,
          elementAgreement: 0.9,
          unsupportedClaimCountAgreement: 0.9,
          unsupportedClaimSpanAgreement: 0.9,
          agreement: 0.9,
        },
      ],
      deltas: [
        {
          metric: "completenessRate",
          pointEstimate: 0.1,
          ciLower: 0.04,
          ciUpper: 0.16,
          significant: true,
        },
        {
          metric: "unsupportedClaims",
          pointEstimate: -0.25,
          ciLower: -0.4,
          ciUpper: -0.1,
          significant: true,
        },
        {
          metric: "citationPrecision",
          pointEstimate: 0.02,
          ciLower: -0.01,
          ciUpper: 0.05,
          significant: false,
        },
        {
          metric: "citationRecall",
          pointEstimate: 0.03,
          ciLower: 0.01,
          ciUpper: 0.05,
          significant: true,
        },
        {
          metric: "fabricatedCitationCount",
          pointEstimate: -0.1,
          ciLower: -0.2,
          ciUpper: 0,
          significant: false,
        },
        {
          metric: "costUsd",
          pointEstimate: 0.03,
          ciLower: 0.02,
          ciUpper: 0.04,
          significant: true,
        },
        {
          metric: "latencyMs",
          pointEstimate: 1_200,
          ciLower: 800,
          ciUpper: 1_600,
          significant: true,
        },
        {
          metric: "modelCalls",
          pointEstimate: 3,
          ciLower: 3,
          ciUpper: 3,
          significant: true,
        },
      ],
    },
    scenarios,
  };
}
