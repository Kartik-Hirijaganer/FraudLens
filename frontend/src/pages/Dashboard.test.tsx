import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { dashboardMetrics, makeClient } from "../test/factories";
import { Dashboard } from "./Dashboard";

afterEach(() => {
  window.location.hash = "";
});

describe("Dashboard", () => {
  it("renders metric cards (model health + counts) and opens an alert", async () => {
    const client = makeClient();
    render(<Dashboard client={client} />);
    expect(await screen.findByText("model-v1")).toBeInTheDocument(); // active model label
    expect(screen.getByText("50")).toBeInTheDocument(); // recent transactions total
    expect(screen.getByText("12")).toBeInTheDocument(); // completed investigations
    expect(screen.getByText("High")).toBeInTheDocument(); // open-alert severity badge
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(window.location.hash).toBe("#/alerts/alert-1");
  });

  it("shows a canary rollout in the model-health card", async () => {
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
    expect(await screen.findByText(/canary model-v2/)).toBeInTheDocument();
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
