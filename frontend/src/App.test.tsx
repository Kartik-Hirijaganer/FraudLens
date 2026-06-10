import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ApiHealth } from "./lib/api";

const HEALTH: ApiHealth = {
  status: "ok",
  service: "FraudLens",
  version: "0.1.0",
  environment: "dev",
};

describe("App", () => {
  it("renders the wise-themed hero headline", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "FraudLens" })).toBeInTheDocument();
  });

  it("shows the API status after a successful health check", async () => {
    const fetcher = vi.fn(() => Promise.resolve(HEALTH));
    render(<App healthFetcher={fetcher} />);
    await userEvent.click(screen.getByRole("button", { name: /check api health/i }));
    expect(await screen.findByText("ok")).toBeInTheDocument();
  });

  it("shows 'unavailable' when the health check fails", async () => {
    const fetcher = vi.fn(() => Promise.reject(new Error("down")));
    render(<App healthFetcher={fetcher} />);
    await userEvent.click(screen.getByRole("button", { name: /check api health/i }));
    expect(await screen.findByText("unavailable")).toBeInTheDocument();
  });
});
