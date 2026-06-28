import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders title, description, actions, and aside slots", () => {
    render(
      <PageHeader
        title="Transactions"
        description="Review queue"
        actions={<button type="button">Refresh</button>}
        aside={<span>Live</span>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Transactions" })).toBeInTheDocument();
    expect(screen.getByText("Review queue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});
