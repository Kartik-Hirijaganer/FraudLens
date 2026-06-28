import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Timeline } from "./Timeline";

describe("Timeline", () => {
  it("renders activity items with metadata and body", () => {
    render(
      <Timeline
        items={[
          {
            id: "a1",
            title: "Escalate",
            meta: "Open to In review",
            body: "Needs review",
          },
        ]}
      />,
    );
    expect(screen.getByText("Escalate")).toBeInTheDocument();
    expect(screen.getByText("Open to In review")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });
});
