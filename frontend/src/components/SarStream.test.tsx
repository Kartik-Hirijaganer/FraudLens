import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SarStream } from "./SarStream";

describe("SarStream", () => {
  it("shows a graceful-degradation note when drafting failed", () => {
    render(<SarStream text="" streaming={false} failed />);
    expect(screen.getByText(/SAR drafting was unavailable/)).toBeInTheDocument();
  });

  it("shows a placeholder before any text streams", () => {
    render(<SarStream text="" streaming={false} />);
    expect(screen.getByText("No SAR draft yet")).toBeInTheDocument();
  });

  it("renders streamed text with a caret while streaming", () => {
    render(<SarStream text="Suspicious activity" streaming />);
    expect(screen.getByText(/Suspicious activity/)).toBeInTheDocument();
    expect(screen.getByText("▍")).toBeInTheDocument();
  });

  it("renders final text without a caret when not streaming", () => {
    render(<SarStream text="Final narrative" streaming={false} />);
    expect(screen.getByText(/Final narrative/)).toBeInTheDocument();
    expect(screen.queryByText("▍")).not.toBeInTheDocument();
  });

  it("formats the markdown draft instead of showing raw asterisks", () => {
    const { container } = render(
      <SarStream text={"## Activity summary\n\n**Subject:** wire transfer"} streaming={false} />,
    );
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Activity summary");
    expect(screen.getByText("Subject:").tagName).toBe("STRONG");
    expect(container.textContent).not.toContain("**");
  });

  it("marks the draft busy while regenerating", () => {
    const { container } = render(<SarStream text="Narrative" streaming={false} regenerating />);
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });
});
