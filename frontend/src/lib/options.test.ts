import { describe, expect, it } from "vitest";

import {
  ALERT_STATUS_OPTIONS,
  CANARY_RAMP_STEPS,
  RISK_BAND_OPTIONS,
  TRAINING_LABEL_OPTIONS,
  extractModelMetrics,
} from "./options";

describe("shared options", () => {
  it("centralizes risk, alert, training, and canary options", () => {
    expect(RISK_BAND_OPTIONS.map((option) => option.value)).toEqual([
      "",
      "low",
      "medium",
      "high",
      "critical",
    ]);
    expect(ALERT_STATUS_OPTIONS.map((option) => option.value)).toEqual([
      "",
      "open",
      "in_review",
      "pending_review",
      "resolved",
      "dismissed",
      "escalated",
    ]);
    expect(ALERT_STATUS_OPTIONS.map((option) => option.label)).toEqual([
      "All",
      "Open",
      "In Review",
      "Pending Review",
      "Completed",
      "Archived",
      "Escalated",
    ]);
    expect(TRAINING_LABEL_OPTIONS).toContainEqual({
      value: "confirmed_fraud",
      label: "Confirmed fraud",
    });
    expect(CANARY_RAMP_STEPS).toEqual([5, 25, 50, 100]);
  });
});

describe("extractModelMetrics", () => {
  it("normalizes loose registry metric JSON", () => {
    expect(extractModelMetrics({ precision: 0.91, recall: 0.82, prAuc: 0.77 })).toEqual({
      precision: 0.91,
      recall: 0.82,
      auc: 0.77,
    });
  });

  it("returns nulls for missing or invalid values", () => {
    expect(extractModelMetrics({ precision: "0.91" })).toEqual({
      precision: null,
      recall: null,
      auc: null,
    });
  });
});
