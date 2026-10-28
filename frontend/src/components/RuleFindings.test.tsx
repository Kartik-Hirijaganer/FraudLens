import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RuleFindings } from "./RuleFindings";

describe("RuleFindings", () => {
  it("shows explicit findings and severity independently of a SAR", () => {
    render(
      <RuleFindings
        hits={[
          {
            code: "structuring",
            ruleType: "structuring",
            severity: "high",
            reason: "3 sub-threshold transactions within 168h suggest structuring.",
          },
        ]}
      />,
    );
    const region = screen.getByRole("region", { name: "Rule findings" });
    expect(within(region).getByText("Structuring")).toBeInTheDocument();
    expect(within(region).getByText("High")).toBeInTheDocument();
    expect(within(region).getByText(/3 sub-threshold transactions/)).toBeInTheDocument();
  });

  it("states when no patterns matched", () => {
    render(<RuleFindings hits={[]} />);
    expect(screen.getByText("No deterministic rule patterns matched.")).toBeInTheDocument();
  });
});
