import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Keep the shell test focused on routing + chrome: stub the data calls the rendered pages
// make so they stay in their loading state (no post-test async updates). Page headers /
// nav render outside the data boundary, which is what we assert here.
vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  const pending = (): Promise<never> => new Promise<never>(() => undefined);
  return {
    ...actual,
    apiClient: {
      ...actual.createApiClient(),
      listAlerts: pending,
      getAlert: pending,
      getDeployment: pending,
      getDashboardMetrics: pending,
      getInvestigation: pending,
      listTransactions: pending,
      listModelVersions: pending,
      listDriftReports: pending,
    },
  };
});

import { App } from "./App";

function goTo(hash: string): void {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new Event("hashchange"));
  });
}

afterEach(() => {
  window.location.hash = "";
});

describe("App shell", () => {
  it("renders the brand and both nav landmarks", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "FraudLens" })).toBeInTheDocument();
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getByRole("link", { name: "Alert review" })).toBeInTheDocument();
    expect(within(primary).getByRole("link", { name: "Investigation" })).toBeInTheDocument();
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).getByRole("link", { name: "Model admin" })).toBeInTheDocument();
  });

  it("routes the dashboard by default and marks its nav active", () => {
    render(<App />);
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("switches pages on hash navigation", () => {
    render(<App />);
    goTo("#/transactions");
    expect(screen.getByRole("heading", { level: 1, name: "Transactions" })).toBeInTheDocument();
    goTo("#/model-admin");
    expect(
      screen.getByRole("heading", { level: 1, name: "Model administration" }),
    ).toBeInTheDocument();
  });

  it("marks the Alert review pill active on an alert-detail route", () => {
    render(<App />);
    goTo("#/alerts/alert-1");
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getByRole("link", { name: "Alert review" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders a not-found state for an unknown route", () => {
    render(<App />);
    goTo("#/nope");
    expect(screen.getByText("Page not found")).toBeInTheDocument();
  });
});
