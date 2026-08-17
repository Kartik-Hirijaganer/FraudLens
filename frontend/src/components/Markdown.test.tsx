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

  it("presents technical SAR values as readable analyst copy", () => {
    const { container } = render(
      <Markdown
        text={
          "**Subject:** Suspected high-risk ach activity\n\n" +
          "A ach transaction of 10400.00 USD triggered rapid_movement (rapid_movement). " +
          "Drivers: amount_log, seconds_since_prev_txn_log."
        }
      />,
    );
    expect(screen.getByText(/Suspected high-risk ACH activity/)).toBeInTheDocument();
    expect(screen.getByText(/An ACH transaction of 10,400.00 USD/)).toBeInTheDocument();
    expect(screen.getByText(/Rapid movement/)).toBeInTheDocument();
    expect(screen.getByText(/Transaction amount \(log scale\)/)).toBeInTheDocument();
    expect(screen.getByText(/Time since previous transaction \(log scale\)/)).toBeInTheDocument();
    expect(container.textContent).not.toContain("rapid_movement");
    expect(container.textContent).not.toContain("amount_log");
    expect(container.textContent?.match(/Rapid movement/g)).toHaveLength(1);
  });
});
