import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatTile } from "./StatTile";

describe("StatTile", () => {
  it("renders a labelled metric with hint", () => {
    render(<StatTile label="Open alerts" value={3} hint="11 total" />);
    expect(screen.getByText("Open alerts")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("11 total")).toBeInTheDocument();
  });

  it("can render inside an existing definition list", () => {
    render(
      <dl>
        <StatTile as="dl" label="Active" value="model-v1" emphasis="md" />
      </dl>,
    );
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("model-v1")).toBeInTheDocument();
  });
});
