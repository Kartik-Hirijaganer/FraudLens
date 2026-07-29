import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  refreshAccessToken: vi.fn(() => Promise.resolve(null)),
  signOutSupabase: vi.fn(),
}));

import {
  ApiError,
  createApiClient,
  fetchApiHealth,
  fetchCurrentUser,
  type ApiHealth,
  type TransactionListResponse,
} from "./api";
import { refreshAccessToken } from "./supabase";
import { getSession, signIn, signOut } from "./session";
import { demoPersona } from "../test/factories";

afterEach(() => {
  signOut();
  vi.mocked(refreshAccessToken).mockReset();
  vi.mocked(refreshAccessToken).mockResolvedValue(null);
});

const HEALTH: ApiHealth = {
  status: "ok",
  service: "FraudLens",
  version: "1.0.0",
  environment: "dev",
};

function fakeResponse(ok: boolean, status: number, body: unknown): Response {
  return { ok, status, json: () => Promise.resolve(body) } as unknown as Response;
}

describe("fetchApiHealth", () => {
  it("requests the health endpoint and returns the parsed body", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, HEALTH)));
    const result = await fetchApiHealth(fetchMock);
    expect(result).toEqual(HEALTH);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health");
  });

  it("throws ApiError (with the status) on a non-ok response", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(false, 503, null)));
    await expect(fetchApiHealth(fetchMock)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("fetchCurrentUser", () => {
  it("requests /me with the supplied bearer token", async () => {
    const currentUser = {
      email: "reviewer@example.test",
      role: "reviewer",
      agencyId: "agency-1",
    };
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, currentUser)));
    const result = await fetchCurrentUser("access-token", fetchMock);
    expect(result).toEqual(currentUser);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/me", {
      headers: { Authorization: "Bearer access-token" },
    });
  });
});

describe("createApiClient", () => {
  it("lists transactions with no query string", async () => {
    const page: TransactionListResponse = { transactions: [], nextCursor: null, total: 0 };
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, page)));
    const result = await createApiClient(fetchMock).listTransactions();
    expect(result).toEqual(page);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/transactions", undefined);
  });

  it("encodes list params into the query string", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(fakeResponse(true, 200, { transactions: [], nextCursor: null, total: 0 })),
    );
    await createApiClient(fetchMock).listTransactions({ limit: 10, riskBand: "high" });
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain("limit=10");
    expect(url).toContain("riskBand=high");
  });

  it("posts JSON bodies with the content-type header", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 202, { percent: 50 })));
    await createApiClient(fetchMock).setCanary("v1", 50);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/model-versions/v1/canary");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ percent: 50 });
  });

  it("attaches an Idempotency-Key when starting an investigation", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 202, { runId: "r1" })));
    await createApiClient(fetchMock).startInvestigation({ transactionId: "tx1" }, "key-123");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-123");
  });

  it("uploads CSV as text/csv", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        fakeResponse(true, 202, {
          jobId: "j1",
          accepted: 1,
          duplicates: 0,
          rejected: 0,
          sampleErrors: [],
        }),
      ),
    );
    await createApiClient(fetchMock).uploadCsv("externalId,amount\nx,1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/transactions/upload");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("text/csv");
  });

  it("builds the SSE stream URL", () => {
    expect(createApiClient().investigationStreamUrl("r9")).toBe("/api/v1/investigations/r9/stream");
  });

  it("attaches the selected demo role to API requests", async () => {
    const role = demoPersona("auditor");
    signIn(role.email, false, role.role);
    const fetchMock = vi.fn(() =>
      Promise.resolve(fakeResponse(true, 200, { transactions: [], nextCursor: null, total: 0 })),
    );
    await createApiClient(fetchMock).listTransactions();
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-FraudLens-Demo-Role"]).toBe("auditor");
    expect(createApiClient().investigationStreamUrl("r9")).toBe("/api/v1/investigations/r9/stream");
  });

  it("refreshes once on 401, retries with the new token, and keeps the session", async () => {
    vi.mocked(refreshAccessToken).mockResolvedValue("token-2");
    signIn("reviewer@example.test", false, "reviewer", "token-1");
    const page: TransactionListResponse = { transactions: [], nextCursor: null, total: 0 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(fakeResponse(false, 401, { code: "unauthorized" }))
      .mockResolvedValueOnce(fakeResponse(true, 200, page));

    await expect(createApiClient(fetchMock).listTransactions()).resolves.toEqual(page);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, retryInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect((retryInit.headers as Record<string, string>).Authorization).toBe("Bearer token-2");
    expect(getSession()?.accessToken).toBe("token-2");
  });

  it("signs out when a 401 cannot be refreshed", async () => {
    vi.mocked(refreshAccessToken).mockResolvedValue(null);
    signIn("reviewer@example.test", false, "reviewer", "token-1");
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        fakeResponse(false, 401, {
          code: "unauthorized",
          message: "missing bearer token",
          requestId: "req-1",
        }),
      ),
    );

    await expect(createApiClient(fetchMock).listTransactions()).rejects.toMatchObject({
      status: 401,
    });
    expect(getSession()).toBeNull();
  });

  it("POSTs to the SAR regenerate endpoint for a run", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(fakeResponse(true, 200, { sarDraftId: "s2", version: 2 })),
    );
    await createApiClient(fetchMock).regenerateSar("r9");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/investigations/r9/sar/regenerate");
    expect(init.method).toBe("POST");
  });

  it("raises ApiError carrying the envelope code on a non-2xx response", async () => {
    const envelope = { code: "duplicate_external_id", message: "dup", requestId: "req-1" };
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(false, 409, envelope)));
    await expect(
      createApiClient(fetchMock).ingestTransaction({
        externalId: "x",
        amount: "1",
        currency: "USD",
        occurredAt: "2026-01-01T00:00:00Z",
        originAccount: "a",
        destAccount: "b",
        channel: "wire",
        country: "US",
      }),
    ).rejects.toMatchObject({ code: "duplicate_external_id", status: 409, requestId: "req-1" });
  });

  it("falls back to a synthetic code when the error body is not JSON", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error("not json")),
      } as unknown as Response),
    );
    await expect(createApiClient(fetchMock).getDeployment()).rejects.toMatchObject({
      code: "http_500",
    });
  });

  it("exercises every endpoint method against the expected path", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, {})));
    const client = createApiClient(fetchMock);
    await client.health();
    await client.me();
    await client.getTransaction("t1");
    await client.ingestBatch({ transactions: [] });
    await client.getInvestigation("r1");
    await client.listAlerts({ status: "open" });
    await client.getAlert("a1");
    await client.actOnAlert("a1", { action: "escalate" });
    await client.reviewSar("a1", { decision: "approve" });
    await client.listModelVersions();
    await client.triggerTraining();
    await client.listTrainingRuns();
    await client.promoteToShadow("v1");
    await client.approveVersion("v1");
    await client.rollbackDeployment();
    await client.evaluateCanary();
    await client.listDriftReports();
    await client.getDashboardMetrics();
    const paths = fetchMock.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(paths).toContain("/api/v1/health");
    expect(paths).toContain("/api/v1/me");
    expect(paths).toContain("/api/v1/alerts/a1/sar/review");
    expect(paths).toContain("/api/v1/model-deployment/rollback");
    expect(paths).toContain("/api/v1/drift-reports");
    expect(paths).toContain("/api/v1/dashboard/metrics");
  });

  it("returns the dashboard metrics aggregate verbatim (camelCase passthrough)", async () => {
    const metrics = {
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
      llmCost: { todayUsd: "0.12", totalUsd: "1.45", draftCount: 9 },
      modelHealth: {
        activeVersionLabel: "model-v1",
        canaryVersionLabel: null,
        canaryPercent: 0,
        recentInferenceCount: 14,
        latestDriftSeverity: "low",
      },
    };
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, metrics)));
    const result = await createApiClient(fetchMock).getDashboardMetrics();
    expect(result).toEqual(metrics);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/dashboard/metrics", undefined);
  });
});
