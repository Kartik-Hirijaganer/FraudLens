import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatAgo,
  formatAlertRef,
  formatCurrency,
  formatDateTime,
  formatDurationMs,
  formatInvestigationRef,
  formatMachineKey,
  formatMaskedAccount,
  formatModelBuild,
  formatModelVersion,
  formatPercent,
  formatTransactionRef,
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

describe("formatDurationMs", () => {
  it("formats milliseconds, seconds, and minutes compactly", () => {
    expect(formatDurationMs(25)).toBe("25 ms");
    expect(formatDurationMs(1_500)).toBe("1.5 s");
    expect(formatDurationMs(65_000)).toBe("1m 5s");
  });

  it("returns a dash for negative or non-finite durations", () => {
    expect(formatDurationMs(-1)).toBe("—");
    expect(formatDurationMs(Number.NaN)).toBe("—");
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

describe("formatTransactionRef", () => {
  it("combines the backend id suffix with the UTC transaction date", () => {
    expect(
      formatTransactionRef("4b335aa3-a07c-4194-a2ea-96ed010dcdbe", "2026-07-01T04:00:00-04:00"),
    ).toBe("TXN-260701-0DCDBE");
  });

  it("keeps a stable id-only fallback when the timestamp is invalid", () => {
    expect(formatTransactionRef("tx-abc123", "not-a-date")).toBe("TXN-ABC123");
    expect(formatTransactionRef("", "2026-07-01T00:00:00Z")).toBe("—");
  });
});

describe("formatMaskedAccount", () => {
  it("shows only the already-masked identifier's final four characters", () => {
    expect(formatMaskedAccount("************9102")).toBe("•••• 9102");
    expect(formatMaskedAccount("DEMO-SYNTH-ACCT-a1b2")).toBe("•••• A1B2");
  });

  it("returns a placeholder when no safe tail exists", () => {
    expect(formatMaskedAccount("****")).toBe("—");
  });
});

describe("formatModelVersion", () => {
  it("normalizes explicit versions to three semantic-version parts", () => {
    expect(formatModelVersion("v0-fixture")).toBe("v0.0.0");
    expect(formatModelVersion("v2.4")).toBe("v2.4.0");
    expect(formatModelVersion("model-v1")).toBe("v1.0.0");
  });

  it("uses a training bundle's feature-spec generation for its compact display version", () => {
    expect(formatModelVersion("xgb-synthetic-fs2-a1b2c3d4e5")).toBe("v2.0.0");
  });

  it("leaves an unstructured registry label intact", () => {
    expect(formatModelVersion("champion-blue")).toBe("champion-blue");
  });

  it("returns a dash for a missing label", () => {
    expect(formatModelVersion(null)).toBe("—");
    expect(formatModelVersion(undefined)).toBe("—");
  });
});

describe("formatModelBuild", () => {
  it("extracts a short build reference from a hash-suffixed registry label", () => {
    expect(formatModelBuild("xgb-synthetic-fs2-a1b2c3d4e5")).toBe("Build a1b2c3d4");
  });

  it("omits build detail when the label has no build hash", () => {
    expect(formatModelBuild("model-v1")).toBeNull();
    expect(formatModelBuild(null)).toBeNull();
  });
});

describe("formatMachineKey", () => {
  it("uses explanatory labels for model features", () => {
    expect(formatMachineKey("amount_log")).toBe("Transaction amount (log scale)");
    expect(formatMachineKey("seconds_since_prev_txn_log")).toBe(
      "Time since previous transaction (log scale)",
    );
  });

  it("falls back to a readable sentence-case label", () => {
    expect(formatMachineKey("rapid_movement")).toBe("Rapid movement");
    expect(formatMachineKey("threshold.evasion")).toBe("Threshold evasion");
    expect(formatMachineKey(" ")).toBe("—");
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
