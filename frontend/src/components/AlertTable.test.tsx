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

  it("renders rows and selects an alert on Open", async () => {
    const onSelect = vi.fn();
    render(
      <AlertTable
        alerts={[alertView({ alertId: "a-9", severity: "critical" })]}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText("Critical")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onSelect).toHaveBeenCalledWith("a-9");
  });
});
