import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { navigate, parseHash, paths, useHashRoute } from "./router";

afterEach(() => {
  window.location.hash = "";
});

describe("parseHash", () => {
  it("routes the known paths", () => {
    expect(parseHash("")).toEqual({ name: "dashboard" });
    expect(parseHash("#/")).toEqual({ name: "dashboard" });
    expect(parseHash("#/transactions")).toEqual({ name: "transactions" });
    expect(parseHash("#/alerts")).toEqual({ name: "alerts" });
    expect(parseHash("#/alerts/a1")).toEqual({ name: "alertDetail", alertId: "a1" });
    expect(parseHash("#/investigations/r1")).toEqual({ name: "investigation", runId: "r1" });
    expect(parseHash("#/model-admin")).toEqual({ name: "modelAdmin" });
  });

  it("routes anything unrecognized to notFound", () => {
    expect(parseHash("#/bogus")).toEqual({ name: "notFound" });
    expect(parseHash("#/alerts/a/b")).toEqual({ name: "notFound" });
  });
});

describe("paths", () => {
  it("builds canonical hrefs", () => {
    expect(paths.dashboard).toBe("#/");
    expect(paths.alertDetail("a1")).toBe("#/alerts/a1");
    expect(paths.investigation("r1")).toBe("#/investigations/r1");
  });
});

describe("navigate + useHashRoute", () => {
  it("navigate sets the location hash", () => {
    navigate("#/alerts");
    expect(window.location.hash).toBe("#/alerts");
  });

  it("useHashRoute reflects the current hash and updates on change", () => {
    window.location.hash = "#/transactions";
    const { result } = renderHook(() => useHashRoute());
    expect(result.current).toEqual({ name: "transactions" });
    act(() => {
      window.location.hash = "#/model-admin";
      window.dispatchEvent(new Event("hashchange"));
    });
    expect(result.current).toEqual({ name: "modelAdmin" });
  });
});
