import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DashboardSkeleton } from "./DashboardSkeleton";

describe("DashboardSkeleton", () => {
  it("renders a decorative placeholder with pulsing skeleton blocks", () => {
    const { container } = render(<DashboardSkeleton />);
    const root = container.querySelector("section");
    expect(root).toHaveAttribute("aria-hidden", "true");
    // One block per KPI card (3 each) + queue header + rows — plenty of placeholders.
    const blocks = container.querySelectorAll(".motion-safe\\:animate-pulse");
    expect(blocks.length).toBeGreaterThan(8);
  });
});
