import { afterEach, describe, expect, it, vi } from "vitest";

import { DEMO_ROLES, currentAnalyst, getSession, signIn, signOut } from "./session";

afterEach(() => {
  signOut();
});

const STORAGE_KEY = "fraudlens.session";

// `readStoredSession` runs once at module import, so exercise it by re-importing a fresh
// module instance (vi.resetModules) with storage pre-seeded.
async function importFreshSession(): Promise<typeof import("./session")> {
  vi.resetModules();
  return import("./session");
}

describe("currentAnalyst", () => {
  it("exposes a display name and non-empty avatar initials", () => {
    expect(currentAnalyst.name).toBeTruthy();
    expect(currentAnalyst.initials).toMatch(/^[A-Z]+$/);
  });
});

describe("DEMO_ROLES", () => {
  it("has unique ids and a complete identity per role", () => {
    const ids = DEMO_ROLES.map((r) => r.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const role of DEMO_ROLES) {
      expect(role.name).toBeTruthy();
      expect(role.tag).toBeTruthy();
      expect(["green", "cyan", "amber", "slate"]).toContain(role.accent);
      expect(role.email).toMatch(/@/);
      expect(role.demoPassword.length).toBeGreaterThan(0);
      expect(role.analyst.initials).toMatch(/^[A-Z]+$/);
    }
  });
});

describe("session store", () => {
  it("is signed out by default", () => {
    expect(getSession()).toBeNull();
  });

  it("signs in and reports the session email", () => {
    signIn("analyst@agency.gov");
    expect(getSession()).toEqual({ email: "analyst@agency.gov" });
  });

  it("signs out and clears the session", () => {
    signIn("analyst@agency.gov");
    signOut();
    expect(getSession()).toBeNull();
  });

  it("uses sessionStorage by default and localStorage when remembered", () => {
    signIn("analyst@agency.gov");
    expect(window.sessionStorage.getItem("fraudlens.session")).not.toBeNull();
    expect(window.localStorage.getItem("fraudlens.session")).toBeNull();

    signIn("analyst@agency.gov", true);
    expect(window.localStorage.getItem("fraudlens.session")).not.toBeNull();
    expect(window.sessionStorage.getItem("fraudlens.session")).toBeNull();
  });
});

describe("session rehydration on load", () => {
  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("rehydrates a session persisted in localStorage", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ email: "kept@agency.gov" }));
    const mod = await importFreshSession();
    expect(mod.getSession()).toEqual({ email: "kept@agency.gov" });
  });

  it("rehydrates a session persisted in sessionStorage", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ email: "tab@agency.gov" }));
    const mod = await importFreshSession();
    expect(mod.getSession()).toEqual({ email: "tab@agency.gov" });
  });

  it("ignores a malformed storage entry", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, "{not-json");
    const mod = await importFreshSession();
    expect(mod.getSession()).toBeNull();
  });

  it("ignores an entry without a usable email", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ email: 123 }));
    const mod = await importFreshSession();
    expect(mod.getSession()).toBeNull();
  });

  it("treats a throwing storage as unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    const mod = await importFreshSession();
    expect(mod.getSession()).toBeNull();
  });
});
