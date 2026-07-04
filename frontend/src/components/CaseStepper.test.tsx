import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CASE_STEPS } from "../lib/investigation";
import { CaseStepper } from "./CaseStepper";

describe("CaseStepper", () => {
  it("marks passed steps done and the current step active", () => {
    render(<CaseStepper steps={CASE_STEPS} currentStep={3} />);
    // Risk, Drivers, Citations are behind the analyst -> three done checkmarks.
    expect(screen.getAllByText("✓")).toHaveLength(3);
    const active = screen.getByText("SAR draft").closest("li");
    expect(active).toHaveAttribute("aria-current", "step");
  });

  it("numbers the active and pending steps instead of a checkmark", () => {
    render(<CaseStepper steps={CASE_STEPS} currentStep={0} />);
    expect(screen.queryByText("✓")).not.toBeInTheDocument();
    // First step active ("1"), Submit pending ("5").
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    const active = screen.getByText("Risk").closest("li");
    expect(active).toHaveAttribute("aria-current", "step");
  });
});
