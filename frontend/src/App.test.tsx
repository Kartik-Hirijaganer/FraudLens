import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEMO_ROLES, signIn, signOut } from "./lib/session";

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
  signOut();
});

describe("App gate", () => {
  it("renders the login screen when there is no session", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to your account" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it("swaps the shell for the login screen after signing out", () => {
    render(<App />);
    act(() => {
      signIn("analyst@agency.gov");
    });
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    act(() => {
      screen.getByRole("button", { name: "Sign out" }).click();
    });
    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to your account" }),
    ).toBeInTheDocument();
    expect(workspace).not.toBeInTheDocument();
  });
});

describe("App shell", () => {
  beforeEach(() => {
    signIn("analyst@agency.gov");
  });

  it("hides admin navigation from analyst sessions", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "FraudLens" })).toBeInTheDocument();
    // The top pill nav was removed — the sidebar is the sole primary navigation.
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).queryByRole("link", { name: "Model admin" })).not.toBeInTheDocument();
    expect(within(workspace).getByRole("link", { name: "Transactions" })).toBeInTheDocument();
  });

  it("shows admin navigation to admin sessions", () => {
    signIn(DEMO_ROLES[2].email, false, "admin");
    render(<App />);
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).getByRole("link", { name: "Model admin" })).toBeInTheDocument();
  });

  it("shows the reviewer persona label", () => {
    signIn(DEMO_ROLES[1].email, false, "reviewer");
    render(<App />);
    expect(screen.getByText("Reviewer")).toBeInTheDocument();
  });

  it("routes the dashboard by default and marks its sidebar nav active", () => {
    render(<App />);
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("switches pages on hash navigation", () => {
    signIn(DEMO_ROLES[2].email, false, "admin");
    render(<App />);
    goTo("#/transactions");
    expect(screen.getByRole("heading", { level: 1, name: "Transactions" })).toBeInTheDocument();
    goTo("#/model-admin");
    expect(
      screen.getByRole("heading", { level: 1, name: "Model administration" }),
    ).toBeInTheDocument();
  });

  it("blocks direct model-admin links for non-admin sessions", () => {
    render(<App />);
    goTo("#/model-admin");
    expect(screen.getByText("Admin only")).toBeInTheDocument();
  });

  it("marks the Alerts sidebar nav active on an alert-detail route", () => {
    render(<App />);
    goTo("#/alerts/alert-1");
    const workspace = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(workspace).getByRole("link", { name: "Alerts" })).toHaveAttribute(
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
