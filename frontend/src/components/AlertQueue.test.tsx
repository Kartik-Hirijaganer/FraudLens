import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { alertView } from "../test/factories";
import { AlertQueue } from "./AlertQueue";

describe("AlertQueue", () => {
  it("renders a row per alert with reference, reason headline, and view-all footer", () => {
    render(
      <AlertQueue
        alerts={[
          alertView({
            alertId: "alert-4821",
            severity: "high",
            amount: "48200.00",
            reviewFlags: [{ flag: "cross_border", reason: "Cross-border wire · new counterparty" }],
          }),
        ]}
        totalOpen={24}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("AL-4821")).toBeInTheDocument();
    expect(screen.getByText("Cross-border wire · new counterparty")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View all 24 alerts/ })).toBeInTheDocument();
  });

  it("orders rows risk-first then most-recent and caps the visible rows", () => {
    const alerts = [
      alertView({ alertId: "alert-1", severity: "medium", createdAt: "2026-06-11T10:00:00Z" }),
      alertView({ alertId: "alert-2", severity: "high", createdAt: "2026-06-11T09:00:00Z" }),
      alertView({ alertId: "alert-3", severity: "high", createdAt: "2026-06-11T11:00:00Z" }),
      alertView({ alertId: "alert-4", severity: "low", createdAt: "2026-06-11T10:00:00Z" }),
      alertView({ alertId: "alert-5", severity: "low", createdAt: "2026-06-11T08:00:00Z" }),
    ];
    render(<AlertQueue alerts={alerts} totalOpen={5} onSelect={vi.fn()} />);
    const refs = screen.getAllByText(/^AL-\d$/).map((node) => node.textContent);
    // High (newest first), then medium; the fifth alert is capped out.
    expect(refs).toEqual(["AL-3", "AL-2", "AL-1", "AL-4"]);
  });

  it("invokes onSelect with the alert id when Review is clicked", async () => {
    const onSelect = vi.fn();
    render(
      <AlertQueue alerts={[alertView({ alertId: "alert-9" })]} totalOpen={1} onSelect={onSelect} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(onSelect).toHaveBeenCalledWith("alert-9");
  });

  it("falls back to a neutral headline when an alert has no review flags", () => {
    render(
      <AlertQueue alerts={[alertView({ reviewFlags: [] })]} totalOpen={1} onSelect={vi.fn()} />,
    );
    expect(screen.getByText("Flagged transaction")).toBeInTheDocument();
  });

  it("shows an empty state and no footer when there are no alerts", () => {
    render(<AlertQueue alerts={[]} totalOpen={0} onSelect={vi.fn()} />);
    expect(screen.getByText("You're all caught up")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /View all/ })).not.toBeInTheDocument();
  });
});
