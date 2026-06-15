import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Keep the shell test focused on routing: stub the data calls the rendered pages make so
// they stay in their loading state (no post-test async updates); page headers render
// outside the data boundary, which is what we assert here.
vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  const pending = (): Promise<never> => new Promise<never>(() => undefined);
  return {
    ...actual,
    apiClient: {
      ...actual.createApiClient(),
      listAlerts: pending,
      getDeployment: pending,
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
  it("renders the brand and primary nav", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "FraudLens" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Model admin" })).toBeInTheDocument();
  });

  it("routes the dashboard by default and marks its nav active", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Investigations" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
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

  it("renders a not-found state for an unknown route", () => {
    render(<App />);
    goTo("#/nope");
    expect(screen.getByText("Page not found")).toBeInTheDocument();
  });
});
