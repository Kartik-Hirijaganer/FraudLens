import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskDot } from "./RiskDot";

describe("RiskDot", () => {
  it("renders a visible risk label when requested", () => {
    render(<RiskDot band="critical" showLabel />);
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("keeps an accessible label when compact", () => {
    render(<RiskDot band="low" />);
    expect(screen.getByText("Low")).toHaveClass("sr-only");
  });
});
