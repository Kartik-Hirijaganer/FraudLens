import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FraudGauge } from "./FraudGauge";

function valueCircle(container: HTMLElement): Element | null {
  return container.querySelector('circle[stroke-linecap="round"]');
}

function installReducedMotion(matches: boolean): void {
  window.matchMedia = vi.fn(() => ({
    matches,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  (window as { matchMedia?: unknown }).matchMedia = undefined;
});

describe("FraudGauge", () => {
  it("renders an accessible meter coloured by band and animates by default", () => {
    const { container } = render(<FraudGauge value={0.82} band="critical" label="risk score" />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "82");
    expect(meter).toHaveAttribute("aria-valuetext", expect.stringContaining("Critical"));
    expect(meter).toHaveAttribute("aria-valuetext", expect.stringContaining("82.0%"));
    expect(valueCircle(container)?.getAttribute("class")).toContain(
      "transition-[stroke-dashoffset]",
    );
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("clamps a non-finite value to zero", () => {
    render(<FraudGauge value={Number.NaN} band="low" label="risk score" />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "0");
  });

  it("skips the transition when reduced motion is preferred", () => {
    installReducedMotion(true);
    const { container } = render(<FraudGauge value={0.5} band="medium" label="risk score" />);
    expect(valueCircle(container)?.getAttribute("class")).not.toContain("transition-");
  });

  it("captions the number with what it actually is, not a fixed 'fraud risk'", () => {
    // A blended policy score and a calibrated probability are different quantities; the caption
    // must follow the field the caller read so neither is ever reported as the other.
    const { rerender } = render(<FraudGauge value={0.26} band="low" label="risk score" />);
    expect(screen.getByText("risk score")).toBeInTheDocument();
    expect(screen.queryByText("fraud risk")).not.toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute(
      "aria-valuetext",
      expect.stringContaining("risk score"),
    );

    rerender(<FraudGauge value={0.001} band="low" label="fraud probability" />);
    expect(screen.getByText("fraud probability")).toBeInTheDocument();
  });
});
