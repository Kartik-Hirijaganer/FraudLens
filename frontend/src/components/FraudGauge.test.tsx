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
    const { container } = render(<FraudGauge value={0.82} band="critical" />);
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
    render(<FraudGauge value={Number.NaN} band="low" />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "0");
  });

  it("skips the transition when reduced motion is preferred", () => {
    installReducedMotion(true);
    const { container } = render(<FraudGauge value={0.5} band="medium" />);
    expect(valueCircle(container)?.getAttribute("class")).not.toContain("transition-");
  });
});
