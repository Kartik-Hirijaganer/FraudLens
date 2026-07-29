import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError, type PortfolioDemoConfig } from "./api";
import { toDemoRoles, usePortfolioDemoPersonas } from "./portfolioDemo";

// A projection payload shaped exactly like the backend's public response. Every persona value
// the picker shows originates here — the frontend declares none of it.
const PROJECTION: PortfolioDemoConfig = {
  storyVersion: "vtest",
  agency: {
    id: "00000000-0000-4000-8000-00000000d3m0",
    name: "Synthetic Test Agency",
    slug: "synthetic-test-agency",
    researchPartitionKey: "Synthetic Test Agency",
  },
  personas: [
    {
      key: "analyst",
      role: "analyst",
      email: "analyst@example.test",
      displayName: "Test Analyst",
      initials: "TA",
      pickerName: "Fraud Analyst",
      pickerTag: "Queue",
      pickerAccent: "green",
    },
    {
      key: "admin",
      role: "admin",
      email: "admin@example.test",
      displayName: "Test Admin",
      initials: "TM",
      pickerName: "Compliance Admin",
      pickerTag: "Model",
      pickerAccent: "amber",
    },
  ],
  syntheticPassword: "synthetic-test-password",
};

describe("toDemoRoles", () => {
  it("maps every picker field from the projection", () => {
    expect(toDemoRoles(PROJECTION)).toEqual([
      {
        id: "analyst",
        role: "analyst",
        name: "Fraud Analyst",
        tag: "Queue",
        accent: "green",
        analyst: { name: "Test Analyst", initials: "TA" },
        email: "analyst@example.test",
        demoPassword: "synthetic-test-password",
        agencyId: PROJECTION.agency.id,
      },
      {
        id: "admin",
        role: "admin",
        name: "Compliance Admin",
        tag: "Model",
        accent: "amber",
        analyst: { name: "Test Admin", initials: "TM" },
        email: "admin@example.test",
        demoPassword: "synthetic-test-password",
        agencyId: PROJECTION.agency.id,
      },
    ]);
  });
});

describe("usePortfolioDemoPersonas", () => {
  it("never requests the projection when the picker is disabled", () => {
    const load = vi.fn(() => Promise.resolve(PROJECTION));
    const { result } = renderHook(() => usePortfolioDemoPersonas(false, load));
    expect(result.current).toEqual({ status: "disabled", personas: [] });
    expect(load).not.toHaveBeenCalled();
  });

  it("reports loading before the projection resolves, then the mapped personas", async () => {
    const load = vi.fn(() => Promise.resolve(PROJECTION));
    const { result } = renderHook(() => usePortfolioDemoPersonas(true, load));
    expect(result.current.status).toBe("loading");
    expect(result.current.personas).toEqual([]);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.personas.map((persona) => persona.email)).toEqual([
      "analyst@example.test",
      "admin@example.test",
    ]);
  });

  it("reports failed when the projection is unavailable (e.g. the demo is off server-side)", async () => {
    const load = vi.fn(() => Promise.reject(new ApiError(404, "not_found", "disabled")));
    const { result } = renderHook(() => usePortfolioDemoPersonas(true, load));
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(result.current.personas).toEqual([]);
  });

  it("stays ready with an empty list when the story configures no personas", async () => {
    const load = vi.fn(() => Promise.resolve({ ...PROJECTION, personas: [] }));
    const { result } = renderHook(() => usePortfolioDemoPersonas(true, load));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.personas).toEqual([]);
  });
});
