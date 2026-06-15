import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Select } from "./Select";

const OPTIONS = [
  { value: "a", label: "Apple" },
  { value: "b", label: "Banana" },
];

describe("Select", () => {
  it("associates the label, lists options, and fires onChange", async () => {
    const onChange = vi.fn();
    render(<Select label="Fruit" options={OPTIONS} value="a" onChange={onChange} />);
    const select = screen.getByLabelText("Fruit");
    expect(select).toHaveValue("a");
    expect(screen.getByRole("option", { name: "Banana" })).toBeInTheDocument();
    await userEvent.selectOptions(select, "b");
    expect(onChange).toHaveBeenCalled();
  });
});
