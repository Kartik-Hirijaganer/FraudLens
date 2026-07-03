import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DecisionRail } from "./DecisionRail";

describe("DecisionRail", () => {
  it("renders the title and children", () => {
    render(
      <DecisionRail title="Actions">
        <button type="button">Resolve</button>
      </DecisionRail>,
    );
    expect(screen.getByRole("heading", { name: "Actions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  });

  it("omits the heading when the title is intentionally blank", () => {
    render(<DecisionRail title="">Quiet controls</DecisionRail>);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByText("Quiet controls")).toBeInTheDocument();
  });
});
