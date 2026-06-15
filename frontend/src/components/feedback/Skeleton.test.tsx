import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Skeleton } from "./Skeleton";

describe("Skeleton", () => {
  it("renders a decorative pulsing block with a default size", () => {
    const { container } = render(<Skeleton />);
    const block = container.firstChild as HTMLElement;
    expect(block).toHaveAttribute("aria-hidden", "true");
    expect(block).toHaveClass("motion-safe:animate-pulse");
    expect(block).toHaveClass("h-lg");
  });

  it("applies a custom size className", () => {
    const { container } = render(<Skeleton className="h-3xl w-1/2" />);
    expect(container.firstChild).toHaveClass("h-3xl");
  });
});
