import { describe, expect, it } from "vitest";

import { riskTone, severityRank } from "./risk";

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

describe("severityRank", () => {
  it("ranks critical above high, medium, and low", () => {
    expect(
      ["medium", "critical", "low", "high"].sort((a, b) => severityRank(b) - severityRank(a)),
    ).toEqual(["critical", "high", "medium", "low"]);
  });

  it("returns zero for missing or unknown values", () => {
    expect(severityRank(null)).toBe(0);
    expect(severityRank("unknown")).toBe(0);
  });
});
