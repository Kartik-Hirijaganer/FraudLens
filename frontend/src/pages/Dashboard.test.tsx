import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { signIn, signOut } from "../lib/session";
import { alertView, dashboardMetrics, makeClient } from "../test/factories";
import { Dashboard } from "./Dashboard";

// The signed-in display identity the shell greets — supplied by the session, never a constant.
const DISPLAY_IDENTITY = { name: "Test Analyst", initials: "TA" };

afterEach(() => {
  cleanup();
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
          total: 24,
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
          total: 2,
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
    expect(screen.getByRole("heading", { name: "Open alerts" })).toBeInTheDocument();
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

  it("does not imply all casework is complete when there are no open alerts", async () => {
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(dashboardMetrics({ alerts: { ...dashboardMetrics().alerts, open: 0 } })),
      ),
      listAlerts: vi.fn(() => Promise.resolve({ alerts: [], total: 0 })),
    });
    render(<Dashboard client={client} />);
    expect(
      await screen.findByText(/Check Alerts for cases already in progress/),
    ).toBeInTheDocument();
    expect(screen.getByText("No open alerts")).toBeInTheDocument();
    expect(screen.queryByText(/all caught up/)).not.toBeInTheDocument();
  });

  it.each(["reviewer", "admin"] as const)(
    "shows independent review work for %s even when no alerts are open",
    async (role) => {
      signIn(`${role}@agency.test`, false, role);
      const client = makeClient({
        getDashboardMetrics: vi.fn(() =>
          Promise.resolve(dashboardMetrics({ alerts: { ...dashboardMetrics().alerts, open: 0 } })),
        ),
        listAlerts: vi.fn(() =>
          Promise.resolve({
            alerts: [alertView({ status: "escalated", amount: "10400" })],
            total: 1,
          }),
        ),
      });
      render(<Dashboard client={client} />);
      expect(
        await screen.findByText("You have 1 case awaiting your independent review."),
      ).toBeInTheDocument();
      expect(client.listAlerts).toHaveBeenCalledWith({ queue: "awaiting_review", limit: 50 });
      expect(screen.getByText(/\$10,400\.00/)).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Awaiting my review" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /View all 1 case awaiting review/ })).toHaveAttribute(
        "href",
        "#/alerts?queue=awaiting_review",
      );
      expect(screen.queryByText(/all caught up/)).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Review" }));
      expect(window.location.hash).toBe("#/alerts/alert-1");
    },
  );

  it("uses the full eligible total, rather than the loaded page or agency-wide escalations", async () => {
    signIn("reviewer@agency.test", false, "reviewer");
    const client = makeClient({
      listAlerts: vi.fn(() => Promise.resolve({ alerts: [alertView()], total: 120 })),
    });
    render(<Dashboard client={client} />);
    expect(
      await screen.findByText("You have 120 cases awaiting your independent review."),
    ).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
  });

  it("shows the correct empty review queue even when other agency submissions exist", async () => {
    signIn("reviewer@agency.test", false, "reviewer");
    const client = makeClient({
      listAlerts: vi.fn(() => Promise.resolve({ alerts: [], total: 0 })),
    });
    render(<Dashboard client={client} />);
    expect(
      await screen.findByText("No cases are awaiting your independent review."),
    ).toBeInTheDocument();
    expect(screen.getByText("No cases awaiting your review")).toBeInTheDocument();
    expect(screen.getByText("2 awaiting review across the agency")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
  });

  it("keeps auditors on the read-only open-alert overview", async () => {
    signIn("auditor@agency.test", false, "auditor");
    const client = makeClient();
    render(<Dashboard client={client} />);
    await screen.findByRole("heading", { name: "Open alerts" });
    expect(client.listAlerts).toHaveBeenCalledWith({ status: "open", limit: 50 });
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
