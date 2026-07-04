import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders headings, bold, and paragraphs without raw markers", () => {
    const { container } = render(
      <Markdown
        text={
          "# Suspicious Activity Report\n\n**Subject:** Suspected activity\n\nA plain paragraph."
        }
      />,
    );
    // The heading text renders inside an <h1>, not as literal "# ...".
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Suspicious Activity Report",
    );
    // Bold markers become <strong>, not visible asterisks.
    expect(screen.getByText("Subject:").tagName).toBe("STRONG");
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("# ");
    expect(screen.getByText(/A plain paragraph\./)).toBeInTheDocument();
  });

  it("renders bullet lists", () => {
    render(<Markdown text={"- first\n- second"} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("first")).toBeInTheDocument();
  });
});
