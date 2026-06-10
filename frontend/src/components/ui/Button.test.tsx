import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./Button";

describe("Button", () => {
  it("renders children, defaults to type=button, and fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    const button = screen.getByRole("button", { name: "Go" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("bg-primary");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies the secondary and tertiary variant classes", () => {
    const { rerender } = render(<Button variant="secondary">x</Button>);
    expect(screen.getByRole("button")).toHaveClass("bg-canvas-soft");
    rerender(<Button variant="tertiary">x</Button>);
    expect(screen.getByRole("button")).toHaveClass("border-ink");
  });
});
