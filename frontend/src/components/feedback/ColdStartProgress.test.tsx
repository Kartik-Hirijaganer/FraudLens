import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ColdStartProgress } from "./ColdStartProgress";

describe("ColdStartProgress", () => {
  it("renders an indeterminate progressbar with the default message", () => {
    render(<ColdStartProgress />);
    expect(screen.getByRole("progressbar", { name: "Starting up" })).toBeInTheDocument();
    expect(screen.getByText(/Waking the service/)).toBeInTheDocument();
  });

  it("honors a custom message", () => {
    render(<ColdStartProgress message="Almost there" />);
    expect(screen.getByText("Almost there")).toBeInTheDocument();
  });
});
