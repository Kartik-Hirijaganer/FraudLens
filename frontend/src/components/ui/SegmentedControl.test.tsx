import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SegmentedControl } from "./SegmentedControl";

describe("SegmentedControl", () => {
  it("renders labelled radio options and reports changes", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        ariaLabel="Filter by status"
        options={[
          { value: "", label: "All" },
          { value: "open", label: "Open" },
        ]}
        value=""
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("radiogroup", { name: "Filter by status" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Open" }));
    expect(onChange).toHaveBeenCalledWith("open");
  });
});
