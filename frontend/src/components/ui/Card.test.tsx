import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card } from "./Card";

describe("Card", () => {
  it("renders children and merges a caller className onto the canvas surface", () => {
    render(<Card className="extra-class">hello</Card>);
    const card = screen.getByText("hello");
    expect(card).toHaveClass("bg-canvas");
    expect(card).toHaveClass("rounded-xl");
    expect(card).toHaveClass("extra-class");
  });
});
