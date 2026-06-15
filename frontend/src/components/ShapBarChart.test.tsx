import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ShapBarChart } from "./ShapBarChart";

describe("ShapBarChart", () => {
  it("renders an empty state with no features", () => {
    render(<ShapBarChart features={[]} />);
    expect(screen.getByText("No explanation yet")).toBeInTheDocument();
  });

  it("renders signed contributions with risk-direction colours", () => {
    const { container } = render(
      <ShapBarChart
        features={[
          { feature: "amount", value: 5, shapValue: 0.3 },
          { feature: "age_days", value: 1, shapValue: -0.1 },
        ]}
      />,
    );
    expect(screen.getByText("amount")).toBeInTheDocument();
    expect(screen.getByText("+0.300")).toBeInTheDocument();
    expect(screen.getByText("-0.100")).toBeInTheDocument();
    expect(container.querySelectorAll(".bg-negative")).toHaveLength(1);
    expect(container.querySelectorAll(".bg-positive")).toHaveLength(1);
  });
});
