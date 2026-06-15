import { describe, expect, it } from "vitest";

import { riskTone } from "./risk";

describe("riskTone", () => {
  it("maps each band/severity onto the semantic palette", () => {
    expect(riskTone("low")).toBe("positive");
    expect(riskTone("medium")).toBe("warning");
    expect(riskTone("high")).toBe("negative");
    expect(riskTone("critical")).toBe("negative");
  });

  it("is case-insensitive", () => {
    expect(riskTone("CRITICAL")).toBe("negative");
  });

  it("falls back to neutral for an unknown value", () => {
    expect(riskTone("mystery")).toBe("neutral");
  });
});
