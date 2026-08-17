import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { signIn, signOut } from "../lib/session";
import { alertView, dashboardMetrics, makeClient } from "../test/factories";
import { Dashboard } from "./Dashboard";

// The signed-in display identity the shell greets — supplied by the session, never a constant.
const DISPLAY_IDENTITY = { name: "Test Analyst", initials: "TA" };

afterEach(() => {
  window.location.hash = "";
  signOut();
});

describe("Dashboard", () => {
  it("greets the analyst and calls out the high-risk backlog", async () => {
    signIn("analyst@agency.gov", false, "analyst", undefined, undefined, DISPLAY_IDENTITY);
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(dashboardMetrics({ alerts: { ...dashboardMetrics().alerts, open: 24 } })),
      ),
      listAlerts: vi.fn(() =>
        Promise.resolve({
          alerts: [
            alertView({ severity: "high" }),
            alertView({ alertId: "alert-2", severity: "medium" }),
          ],
        }),
      ),
    });
    render(<Dashboard client={client} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: new RegExp(DISPLAY_IDENTITY.name) }),
    ).toBeInTheDocument();
    expect(screen.getByText(/24 open alerts/)).toBeInTheDocument();
    expect(screen.getByText(/1 high-risk one\b/)).toBeInTheDocument();
  });

  it("greets without a name when there is no session identity", async () => {
    render(<Dashboard client={makeClient()} />);
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).not.toContain(",");
  });

  it("notes when no open alerts are high-risk", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(dashboardMetrics({ alerts: { ...dashboardMetrics().alerts, open: 2 } })),
      ),
      listAlerts: vi.fn(() =>
        Promise.resolve({
          alerts: [
            alertView({ severity: "medium" }),
            alertView({ alertId: "alert-2", severity: "low" }),
          ],
        }),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText(/None are high-risk right now/)).toBeInTheDocument();
  });

  it("renders KPI cards (severity mix + model health) and opens an alert", async () => {
    const client = makeClient();
    render(<Dashboard client={client} />);
    expect(await screen.findByText("v1.0.0")).toBeInTheDocument(); // presentable active model version
    expect(screen.getByText("Open alerts")).toBeInTheDocument();
    expect(screen.getByText("1 high · 0 medium · 0 low")).toBeInTheDocument(); // derived from the open alert
    expect(screen.getByText(/Healthy · drift low/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(window.location.hash).toBe("#/alerts/alert-1");
  });

  it("renders the risk-band mix from the already-fetched metrics, with band deep links", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            transactions: {
              total: 20,
              byRiskBand: { low: 6, medium: 4, high: 3, critical: 2, unscored: 5 },
            },
          }),
        ),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText("Transactions by risk band")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /High/ })).toHaveAttribute(
      "href",
      "#/transactions?riskBand=high",
    );
    // One aggregate request feeds both the KPI cards and the band mix.
    expect(client.getDashboardMetrics).toHaveBeenCalledOnce();
  });

  it("humanizes the active model label (drops the -fixture tag)", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            modelHealth: {
              activeVersionLabel: "v0-fixture",
              canaryVersionLabel: null,
              canaryPercent: 0,
              recentInferenceCount: 1,
              latestDriftSeverity: null,
            },
          }),
        ),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText("v0.0.0")).toBeInTheDocument();
    expect(screen.queryByText("v0-fixture")).not.toBeInTheDocument();
  });

  it("presents a technical training label as a version plus traceable build", async () => {
    const registryId = "xgb-synthetic-fs2-a1b2c3d4e5";
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            modelHealth: {
              activeVersionLabel: registryId,
              canaryVersionLabel: null,
              canaryPercent: 0,
              recentInferenceCount: 1,
              latestDriftSeverity: null,
            },
          }),
        ),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText("v2.0.0")).toBeInTheDocument();
    expect(screen.getByText("Build a1b2c3d4")).toHaveAttribute(
      "title",
      `Registry ID: ${registryId}`,
    );
  });

  it("shows the dashboard skeleton while data is loading", () => {
    const pending = (): Promise<never> => new Promise<never>(() => undefined);
    render(
      <Dashboard client={makeClient({ getDashboardMetrics: pending, listAlerts: pending })} />,
    );
    // The skeleton is decorative (aria-hidden) and pulses via motion-safe.
    expect(document.querySelector(".motion-safe\\:animate-pulse")).toBeInTheDocument();
  });

  it("shows a canary rollout in the active-model card", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            modelHealth: {
              activeVersionLabel: "model-v1",
              canaryVersionLabel: "model-v2",
              canaryPercent: 25,
              recentInferenceCount: 5,
              latestDriftSeverity: "low",
            },
          }),
        ),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText(/Canary v2.0.0 · 25% traffic/)).toBeInTheDocument();
  });

  it("degrades a missing active model to a dash", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            modelHealth: {
              activeVersionLabel: null,
              canaryVersionLabel: null,
              canaryPercent: 0,
              recentInferenceCount: 0,
              latestDriftSeverity: null,
            },
          }),
        ),
      ),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText("—")).toBeInTheDocument();
    expect(screen.getByText("No model promoted")).toBeInTheDocument();
  });

  it("shows an all-clear message when there are no open alerts", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(dashboardMetrics({ alerts: { ...dashboardMetrics().alerts, open: 0 } })),
      ),
      listAlerts: vi.fn(() => Promise.resolve({ alerts: [] })),
    });
    render(<Dashboard client={client} />);
    expect(await screen.findByText(/You're all caught up — no open alerts/)).toBeInTheDocument();
    expect(screen.getByText("You're all caught up")).toBeInTheDocument(); // queue empty state
  });

  it("shows an error state with a working retry when loading fails", async () => {
    const getDashboardMetrics = vi.fn(() =>
      Promise.reject(new ApiError(500, "server_error", "boom")),
    );
    render(<Dashboard client={makeClient({ getDashboardMetrics })} />);
    expect(await screen.findByText("Request failed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(getDashboardMetrics).toHaveBeenCalledTimes(2));
  });
});
