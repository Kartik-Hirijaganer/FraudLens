import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Textarea } from "./Textarea";

describe("Textarea", () => {
  it("associates the label and accepts input", async () => {
    render(<Textarea label="Note" />);
    const area = screen.getByLabelText("Note");
    await userEvent.type(area, "hello");
    expect(area).toHaveValue("hello");
  });
});
