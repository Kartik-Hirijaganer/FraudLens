import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge } from "./Badge";

describe("Badge", () => {
  it("renders the positive tone by default and the negative tone on request", () => {
    const { rerender } = render(<Badge>OK</Badge>);
    expect(screen.getByText("OK")).toHaveClass("bg-primary-pale");
    rerender(<Badge tone="negative">BAD</Badge>);
    expect(screen.getByText("BAD")).toHaveClass("bg-negative-bg");
  });

  it("renders the warning and neutral tones", () => {
    const { rerender } = render(<Badge tone="warning">WARN</Badge>);
    expect(screen.getByText("WARN")).toHaveClass("bg-warning");
    rerender(<Badge tone="neutral">MEH</Badge>);
    expect(screen.getByText("MEH")).toHaveClass("bg-canvas-soft");
  });
});
