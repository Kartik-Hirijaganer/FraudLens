import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { currentAnalyst } from "../lib/session";
import { alertView, dashboardMetrics, makeClient } from "../test/factories";
import { Dashboard } from "./Dashboard";

afterEach(() => {
  window.location.hash = "";
});

describe("Dashboard", () => {
  it("greets the analyst and calls out the high-risk backlog", async () => {
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
      await screen.findByRole("heading", { level: 1, name: new RegExp(currentAnalyst.name) }),
    ).toBeInTheDocument();
    expect(screen.getByText(/24 open alerts/)).toBeInTheDocument();
    expect(screen.getByText(/1 high-risk one\b/)).toBeInTheDocument();
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
    expect(await screen.findByText("model-v1")).toBeInTheDocument(); // active model label
    expect(screen.getByText("Open alerts")).toBeInTheDocument();
    expect(screen.getByText("1 high · 0 medium · 0 low")).toBeInTheDocument(); // derived from the open alert
    expect(screen.getByText(/Healthy · drift low/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(window.location.hash).toBe("#/alerts/alert-1");
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
    expect(await screen.findByText(/Canary model-v2 @ 25%/)).toBeInTheDocument();
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
