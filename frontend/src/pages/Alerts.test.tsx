import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { alertView, makeClient } from "../test/factories";
import { Alerts } from "./Alerts";

afterEach(() => {
  window.location.hash = "";
});

describe("Alerts", () => {
  it("lists alerts and opens one", async () => {
    render(<Alerts client={makeClient()} />);
    expect(await screen.findByText("High")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(window.location.hash).toBe("#/alerts/alert-1");
  });

  it("re-queries when the status filter changes", async () => {
    const listAlerts = vi.fn(() => Promise.resolve({ alerts: [alertView()], total: 1 }));
    render(<Alerts client={makeClient({ listAlerts })} />);
    await screen.findByText("High");
    await userEvent.click(screen.getByRole("radio", { name: "Available" }));
    await waitFor(() =>
      expect(listAlerts).toHaveBeenCalledWith({ queue: "available", limit: 100 }),
    );
    await userEvent.click(screen.getByRole("radio", { name: "Awaiting my review" }));
    await waitFor(() =>
      expect(listAlerts).toHaveBeenCalledWith({ queue: "awaiting_review", limit: 100 }),
    );
  });

  it("shows an error state on failure", async () => {
    const client = makeClient({
      listAlerts: vi.fn(() => Promise.reject(new ApiError(500, "x", "boom"))),
    });
    render(<Alerts client={client} />);
    expect(await screen.findByText("Request failed")).toBeInTheDocument();
  });

  it("opens the filtered review queue from a dashboard link and resets through All cases", async () => {
    window.location.hash = "#/alerts?queue=awaiting_review";
    const client = makeClient();
    render(<Alerts client={client} />);
    await screen.findByText("High");
    expect(screen.getByRole("radio", { name: "Awaiting my review" })).toBeChecked();
    expect(client.listAlerts).toHaveBeenCalledWith({ queue: "awaiting_review", limit: 100 });
    await userEvent.click(screen.getByRole("radio", { name: "All cases" }));
    await waitFor(() =>
      expect(client.listAlerts).toHaveBeenLastCalledWith({ queue: undefined, limit: 100 }),
    );
    expect(window.location.hash).toBe("#/alerts");
    expect(screen.getByRole("radio", { name: "All cases" })).toBeChecked();
  });
});
