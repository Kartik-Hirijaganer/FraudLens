import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "./MetricCard";

describe("MetricCard", () => {
  it("renders the label, value, and hint", () => {
    render(<MetricCard label="Open alerts" value={24} hint="6 high · 9 medium · 9 low" />);
    expect(screen.getByText("Open alerts")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    expect(screen.getByText("6 high · 9 medium · 9 low")).toBeInTheDocument();
  });

  it("renders a status dot when a hint tone is supplied", () => {
    const { container } = render(
      <MetricCard
        label="Active model"
        value="v2.4"
        hint="Healthy · drift low"
        hintTone="positive"
      />,
    );
    expect(container.querySelector(".bg-positive")).toBeInTheDocument();
  });

  it("omits the hint line entirely when no hint is given", () => {
    render(<MetricCard label="Active model" value="v2.4" />);
    expect(screen.getByText("v2.4")).toBeInTheDocument();
    expect(screen.queryByText(/drift/)).not.toBeInTheDocument();
  });

  it("renders compact detail separately from the main value", () => {
    render(<MetricCard label="Active model" value="v2.0.0" detail="Build 9d43c5f9" />);
    expect(screen.getByText("Build 9d43c5f9")).toHaveClass("text-caption");
  });
});
