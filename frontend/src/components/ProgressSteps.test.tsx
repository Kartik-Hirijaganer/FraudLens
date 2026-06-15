import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressSteps } from "./ProgressSteps";

describe("ProgressSteps", () => {
  it("marks completed steps done and the next one active while running", () => {
    render(<ProgressSteps completedSteps={["rules", "scoring"]} status="running" />);
    expect(screen.getAllByText("✓")).toHaveLength(2);
    const active = screen.getByText("Explain (SHAP)").closest("li");
    expect(active).toHaveAttribute("aria-current", "step");
  });

  it("marks the next step failed when the run failed", () => {
    render(<ProgressSteps completedSteps={["rules"]} status="failed" />);
    expect(screen.getByText("!")).toBeInTheDocument();
  });

  it("marks every step done when completed", () => {
    render(
      <ProgressSteps
        completedSteps={["rules", "scoring", "shap", "rag", "sar"]}
        status="completed"
      />,
    );
    expect(screen.getAllByText("✓")).toHaveLength(5);
  });
});
