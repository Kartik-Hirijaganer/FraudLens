import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("uses a real controlling button and keeps the hidden panel mounted", async () => {
    render(<Disclosure summary="Provenance">Persistent panel</Disclosure>);

    const button = screen.getByRole("button", { name: /Provenance/ });
    const panel = screen.getByText("Persistent panel");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", panel?.id);
    expect(panel).toHaveAttribute("hidden");

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("hidden");
  });
});
