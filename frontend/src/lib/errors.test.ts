import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { describeError } from "./errors";

describe("describeError", () => {
  it("maps a known envelope code to curated, non-critical copy", () => {
    const described = describeError(new ApiError(409, "duplicate_external_id", "dup"));
    expect(described.title).toBe("Already ingested");
    expect(described.critical).toBe(false);
    expect(described.code).toBe("duplicate_external_id");
  });

  it("marks a flagged code critical even on a 4xx", () => {
    const described = describeError(new ApiError(503, "investigations_unavailable", "x"));
    expect(described.critical).toBe(true);
  });

  it("explains guided case-resolution conflicts", () => {
    expect(
      describeError(new ApiError(409, "sar_decision_required", "server copy")).description,
    ).toMatch(/Approve or reject/);
    expect(describeError(new ApiError(409, "resolution_label_mismatch", "server copy")).title).toBe(
      "Outcome doesn't match",
    );
  });

  it("falls back to the envelope message for an unknown code", () => {
    const described = describeError(new ApiError(500, "weird_code", "boom"));
    expect(described.title).toBe("Request failed");
    expect(described.description).toBe("boom");
    expect(described.critical).toBe(true);
  });

  it("falls back to a status generic when the message is empty", () => {
    const described = describeError(new ApiError(401, "unknown", ""));
    expect(described.description).toBe("Please sign in again.");
  });

  it("treats a non-API error as a connectivity failure", () => {
    const described = describeError(new Error("network down"));
    expect(described.code).toBe("network_error");
    expect(described.critical).toBe(true);
  });

  it("maps status-based generics when no message is present", () => {
    expect(describeError(new ApiError(403, "x", "")).description).toBe(
      "You don't have access to that.",
    );
    expect(describeError(new ApiError(429, "x", "")).description).toBe(
      "Too many requests — please slow down.",
    );
    expect(describeError(new ApiError(500, "x", "")).description).toBe(
      "The server had a problem. Please try again.",
    );
    expect(describeError(new ApiError(400, "x", "")).description).toBe(
      "Something went wrong. Please try again.",
    );
  });
});
