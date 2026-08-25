import { describe, expect, it } from "vitest";

import {
  agentGlyph,
  agentTone,
  riskTone,
  severityCounts,
  severityRank,
  toneDotClass,
} from "./risk";

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

describe("agent presentation", () => {
  it("uses semantic tones and non-colour glyphs for lifecycle states", () => {
    expect(agentTone("completed")).toBe("positive");
    expect(agentTone("degraded")).toBe("warning");
    expect(agentTone("failed")).toBe("negative");
    expect(agentTone("running")).toBe("neutral");
    expect(agentGlyph("completed")).toBe("✓");
    expect(agentGlyph("degraded")).toBe("!");
    expect(agentGlyph("failed")).toBe("×");
    expect(agentGlyph("skipped")).toBe("–");
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

describe("severityCounts", () => {
  it("tallies severities and folds critical into high", () => {
    expect(severityCounts(["high", "critical", "medium", "low", "low", "HIGH"])).toEqual({
      high: 3,
      medium: 1,
      low: 2,
    });
  });

  it("ignores unknown values and returns zeros for an empty list", () => {
    expect(severityCounts(["mystery"])).toEqual({ high: 0, medium: 0, low: 0 });
    expect(severityCounts([])).toEqual({ high: 0, medium: 0, low: 0 });
  });
});

describe("toneDotClass", () => {
  it("maps each tone onto its indicator-dot background class", () => {
    expect(toneDotClass("positive")).toBe("bg-positive");
    expect(toneDotClass("warning")).toBe("bg-warning");
    expect(toneDotClass("negative")).toBe("bg-negative");
    expect(toneDotClass("neutral")).toBe("bg-mute");
  });
});
