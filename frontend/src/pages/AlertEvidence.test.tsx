import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { snapshot } from "../test/factories";
import { AlertRiskEvidence } from "./AlertEvidence";

describe("Saved risk evidence", () => {
  it("keeps rule findings available when scoring and SHAP evidence are unavailable", () => {
    render(
      <AlertRiskEvidence
        investigation={snapshot({
          riskScore: null,
          fraudProbability: null,
          riskBand: null,
          topFeatures: [],
        })}
      />,
    );

    expect(screen.getByText("No persisted risk score is available.")).toBeVisible();
    expect(screen.getByText("No explanation yet")).toBeVisible();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.queryByText(/Calibrated model probability:/)).not.toBeInTheDocument();
    const findings = screen.getByRole("region", { name: "Rule findings" });
    expect(within(findings).getByText("Structuring")).toBeVisible();
    expect(within(findings).getByText("near threshold")).toBeVisible();
  });

  it("labels a probability-only fallback without presenting it as a policy score", () => {
    render(
      <AlertRiskEvidence
        investigation={snapshot({ riskScore: null, fraudProbability: 0.028, riskBand: null })}
      />,
    );

    expect(screen.getByRole("meter")).toHaveAttribute(
      "aria-valuetext",
      expect.stringContaining("2.8% fraud probability"),
    );
    expect(screen.getByText("Calibrated model probability: 2.8%")).toBeVisible();
    expect(screen.getByRole("region", { name: "Rule findings" })).toBeVisible();
  });
});
