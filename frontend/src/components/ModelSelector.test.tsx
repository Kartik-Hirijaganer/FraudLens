import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { modelVersion } from "../test/factories";
import { ModelSelector } from "./ModelSelector";

describe("ModelSelector", () => {
  it("lists the active model and versions, and clears the override on the active option", async () => {
    const onChange = vi.fn();
    render(
      <ModelSelector
        versions={[modelVersion({ versionLabel: "model-v2", status: "shadow" })]}
        activeLabel="model-v1"
        value="model-v2"
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("option", { name: "Active model — v1.0.0" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "v2.0.0 (shadow)" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Score with model"), "");
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("labels the default option when there is no active model", () => {
    render(<ModelSelector versions={[]} activeLabel={null} onChange={vi.fn()} />);
    expect(screen.getByRole("option", { name: "Active model (default)" })).toBeInTheDocument();
  });
});
