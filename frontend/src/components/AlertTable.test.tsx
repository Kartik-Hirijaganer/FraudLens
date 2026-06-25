import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { alertView } from "../test/factories";
import { AlertTable } from "./AlertTable";

describe("AlertTable", () => {
  it("renders an empty state with no alerts", () => {
    render(<AlertTable alerts={[]} onSelect={vi.fn()} />);
    expect(screen.getByText("No alerts")).toBeInTheDocument();
  });

  it("renders rows and selects an alert on Review", async () => {
    const onSelect = vi.fn();
    render(
      <AlertTable
        alerts={[alertView({ alertId: "a-9", severity: "critical", status: "dismissed" })]}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getByText("$9,500.00")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(onSelect).toHaveBeenCalledWith("a-9");
  });
});
