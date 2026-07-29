import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { dashboardMetrics } from "../test/factories";
import { RiskBandBar } from "./RiskBandBar";

// The band mix comes from the dashboard aggregate, so the fixture is the same factory the
// Dashboard test uses — a test never types a count the API did not return.
function metricsWith(byRiskBand: Record<string, number>, total = 20) {
  return dashboardMetrics({ transactions: { total, byRiskBand } }).transactions;
}

// A chip is one list item: the band's label plus its count.
function chipText(label: string): string {
  return screen.getByText(label).closest("li")?.textContent ?? "";
}

describe("RiskBandBar", () => {
  it("renders every band and the unscored bucket with its count", () => {
    render(
      <RiskBandBar
        metrics={metricsWith({ low: 6, medium: 4, high: 3, critical: 2, unscored: 5 })}
      />,
    );
    expect(screen.getByText("Transactions by risk band")).toBeInTheDocument();
    expect(chipText("Low")).toContain("6");
    expect(chipText("Medium")).toContain("4");
    expect(chipText("High")).toContain("3");
    expect(chipText("Critical")).toContain("2");
    expect(chipText("Unscored")).toContain("5");
  });

  it("reads an absent band as zero (byRiskBand is an open map)", () => {
    render(<RiskBandBar metrics={metricsWith({ high: 3 }, 3)} />);
    for (const label of ["Low", "Medium", "Critical", "Unscored"]) {
      expect(chipText(label)).toContain("0");
    }
    expect(chipText("High")).toContain("3");
  });

  it("renders an explicit zero rather than hiding an empty band", () => {
    render(<RiskBandBar metrics={metricsWith({ low: 0, unscored: 0 }, 0)} />);
    expect(chipText("Low")).toContain("0");
    expect(chipText("Unscored")).toContain("0");
  });

  it("deep links each scored band to the band-filtered transactions route", () => {
    render(
      <RiskBandBar
        metrics={metricsWith({ low: 6, medium: 4, high: 3, critical: 2, unscored: 5 })}
      />,
    );
    for (const [label, band] of [
      ["Low", "low"],
      ["Medium", "medium"],
      ["High", "high"],
      ["Critical", "critical"],
    ]) {
      expect(screen.getByRole("link", { name: new RegExp(label) })).toHaveAttribute(
        "href",
        `#/transactions?riskBand=${band}`,
      );
    }
  });

  it("does not link the unscored chip (the API cannot filter on it)", () => {
    render(<RiskBandBar metrics={metricsWith({ unscored: 5 })} />);
    expect(screen.queryByRole("link", { name: /Unscored/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(4);
  });

  it("labels the region and keeps the chips keyboard-reachable without tabindex juggling", () => {
    const { container } = render(<RiskBandBar metrics={metricsWith({ high: 1 })} />);
    const region = container.querySelector("section[aria-labelledby]");
    expect(region).not.toBeNull();
    expect(
      document.getElementById(region?.getAttribute("aria-labelledby") ?? ""),
    ).toHaveTextContent("Transactions by risk band");
    // Real anchors are focusable on their own; nothing fakes focus with a tabindex.
    expect(container.querySelectorAll("[tabindex]")).toHaveLength(0);
  });
});
