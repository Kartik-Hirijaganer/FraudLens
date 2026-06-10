/**
 * Template for a frontend component test. Copy next to the component as
 * <Component>.test.tsx and replace the body. Excluded from the vitest run
 * (see vitest.config.ts `exclude`) so it is a copy-me example, not a live test.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("ExampleComponent", () => {
  it("renders its label", () => {
    render(<span>example</span>);
    expect(screen.getByText("example")).toBeInTheDocument();
  });
});
