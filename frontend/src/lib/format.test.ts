import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatAgo,
  formatAlertRef,
  formatCurrency,
  formatDateTime,
  formatInvestigationRef,
  formatModelVersion,
  formatPercent,
  greeting,
  humanize,
} from "./format";

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

describe("formatAgo", () => {
  const now = new Date("2026-06-13T12:00:00Z");

  it("renders a consistent 'N... ago' phrase for minutes, hours, and days", () => {
    expect(formatAgo("2026-06-13T11:58:00Z", now)).toBe("2m ago");
    expect(formatAgo("2026-06-13T09:00:00Z", now)).toBe("3h ago");
    expect(formatAgo("2026-06-10T12:00:00Z", now)).toBe("3d ago");
  });

  it("clamps a future timestamp to '0m ago' rather than going negative", () => {
    expect(formatAgo("2026-06-13T12:05:00Z", now)).toBe("0m ago");
  });

  it("returns a dash for an unparseable date", () => {
    expect(formatAgo("nope", now)).toBe("—");
  });
});

describe("formatAlertRef", () => {
  it("turns an alert-prefixed id into an AL- reference", () => {
    expect(formatAlertRef("alert-4821")).toBe("AL-4821");
    expect(formatAlertRef("alert_1")).toBe("AL-1");
  });

  it("normalizes an id already in AL form", () => {
    expect(formatAlertRef("al_4821")).toBe("AL-4821");
  });

  it("shortens a long id (e.g. a UUID) to a compact 4-char code", () => {
    expect(formatAlertRef("267ad722-718b-4095-9a7a-46245a094e18")).toBe("AL-4E18");
    expect(formatAlertRef("9f3c")).toBe("AL-9F3C");
  });

  it("returns a dash for empty input", () => {
    expect(formatAlertRef("   ")).toBe("—");
  });
});

describe("formatInvestigationRef", () => {
  it("formats run identifiers without implying that an alert exists", () => {
    expect(formatInvestigationRef("run-4821")).toBe("INV-4821");
    expect(formatInvestigationRef("267ad722-718b-4095-9a7a-46245a094e18")).toBe("INV-4E18");
  });

  it("returns a dash for empty input", () => {
    expect(formatInvestigationRef("   ")).toBe("—");
  });
});

describe("formatModelVersion", () => {
  it("drops the internal -fixture tag and leaves clean labels untouched", () => {
    expect(formatModelVersion("v0-fixture")).toBe("v0");
    expect(formatModelVersion("v2.4")).toBe("v2.4");
    expect(formatModelVersion("model-v1")).toBe("model-v1");
  });

  it("returns a dash for a missing label", () => {
    expect(formatModelVersion(null)).toBe("—");
    expect(formatModelVersion(undefined)).toBe("—");
  });
});

describe("greeting", () => {
  it("picks the greeting by time of day", () => {
    expect(greeting(new Date("2026-06-13T08:00:00"))).toBe("Good morning");
    expect(greeting(new Date("2026-06-13T13:00:00"))).toBe("Good afternoon");
    expect(greeting(new Date("2026-06-13T20:00:00"))).toBe("Good evening");
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
