import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TextInput } from "./TextInput";

describe("TextInput", () => {
  it("associates a generated id with the label", () => {
    render(<TextInput label="Agency ID" />);
    expect(screen.getByLabelText("Agency ID")).toBeInTheDocument();
  });

  it("uses an explicitly supplied id when provided", () => {
    render(<TextInput label="Name" id="custom-id" />);
    expect(screen.getByLabelText("Name")).toHaveAttribute("id", "custom-id");
  });
});
