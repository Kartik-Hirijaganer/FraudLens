import { describe, expect, it } from "vitest";

import { formatAge, formatCurrency, formatDateTime, formatPercent, humanize } from "./format";

describe("formatCurrency", () => {
  it("formats a numeric or string amount with the currency", () => {
    expect(formatCurrency("1234.5", "USD")).toContain("1,234.5");
    expect(formatCurrency(10, "USD")).toContain("10");
  });

  it("returns a dash for a non-finite amount", () => {
    expect(formatCurrency("not-a-number", "USD")).toBe("—");
  });

  it("falls back to a plain number + code for an invalid currency code", () => {
    expect(formatCurrency(10, "US")).toBe("10 US");
  });
});

describe("formatPercent", () => {
  it("renders a fraction as a percentage with one decimal by default", () => {
    expect(formatPercent(0.873)).toBe("87.3%");
  });

  it("honors a custom digit count", () => {
    expect(formatPercent(0.5, 0)).toBe("50%");
  });

  it("returns a dash for a non-finite value", () => {
    expect(formatPercent(Number.NaN)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("renders a valid ISO timestamp", () => {
    expect(formatDateTime("2026-06-13T12:00:00Z")).toContain("2026");
  });

  it("returns a dash for an unparseable date", () => {
    expect(formatDateTime("nope")).toBe("—");
  });
});

describe("formatAge", () => {
  const now = new Date("2026-06-13T12:00:00Z");

  it("renders compact minutes, hours, and days", () => {
    expect(formatAge("2026-06-13T11:38:00Z", now)).toBe("22m");
    expect(formatAge("2026-06-13T09:00:00Z", now)).toBe("3h");
    expect(formatAge("2026-06-01T12:00:00Z", now)).toBe("12d ago");
  });

  it("returns a dash for an unparseable date", () => {
    expect(formatAge("nope", now)).toBe("—");
  });
});

describe("humanize", () => {
  it("turns snake_case and dotted codes into Title Case", () => {
    expect(humanize("in_review")).toBe("In Review");
    expect(humanize("step.rules.completed")).toBe("Step Rules Completed");
  });

  it("returns a dash for empty input", () => {
    expect(humanize("")).toBe("—");
  });
});
