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
    const listAlerts = vi.fn(() => Promise.resolve({ alerts: [alertView()] }));
    render(<Alerts client={makeClient({ listAlerts })} />);
    await screen.findByText("High");
    await userEvent.click(screen.getByRole("radio", { name: "Open" }));
    await waitFor(() => expect(listAlerts).toHaveBeenCalledWith({ status: "open", limit: 100 }));
    await userEvent.click(screen.getByRole("radio", { name: "Pending Review" }));
    await waitFor(() =>
      expect(listAlerts).toHaveBeenCalledWith({ status: "pending_review", limit: 100 }),
    );
  });

  it("shows an error state on failure", async () => {
    const client = makeClient({
      listAlerts: vi.fn(() => Promise.reject(new ApiError(500, "x", "boom"))),
    });
    render(<Alerts client={client} />);
    expect(await screen.findByText("Request failed")).toBeInTheDocument();
  });
});
