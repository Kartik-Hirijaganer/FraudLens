import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEMO_ROLES,
  currentAnalyst,
  getSession,
  hasPermission,
  roleHasPermission,
  signIn,
  signOut,
} from "./session";

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
      expect(["auditor", "analyst", "reviewer", "admin"]).toContain(role.role);
      expect(role.tag).toBeTruthy();
      expect(["green", "cyan", "amber", "slate"]).toContain(role.accent);
      expect(role.email).toMatch(/@/);
      expect(role.demoPassword.length).toBeGreaterThan(0);
      expect(role.analyst.initials).toMatch(/^[A-Z]+$/);
    }
    expect(DEMO_ROLES.find((role) => role.role === "reviewer")?.name).toBe("Reviewer");
  });
});

describe("session store", () => {
  it("is signed out by default", () => {
    expect(getSession()).toBeNull();
  });

  it("signs in and reports the session email", () => {
    signIn("analyst@agency.gov");
    expect(getSession()).toMatchObject({ email: "analyst@agency.gov", role: "analyst" });
  });

  it("derives the selected demo role identity", () => {
    signIn(DEMO_ROLES[2].email, false, DEMO_ROLES[2].role);
    expect(getSession()).toMatchObject({
      email: DEMO_ROLES[2].email,
      role: "admin",
      analyst: DEMO_ROLES[2].analyst,
      demoRole: "admin",
    });
  });

  it("maps legacy demo emails to the same role identities", () => {
    signIn("auditor@fraudlens.demo");
    const auditor = DEMO_ROLES.find((role) => role.role === "auditor");
    expect(getSession()).toMatchObject({
      email: "auditor@fraudlens.demo",
      role: "auditor",
      analyst: auditor?.analyst,
      demoRole: "auditor",
    });
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
    expect(mod.getSession()).toMatchObject({ email: "kept@agency.gov", role: "analyst" });
  });

  it("rehydrates a session persisted in sessionStorage", async () => {
    window.sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ email: "tab@agency.gov", role: "auditor" }),
    );
    const mod = await importFreshSession();
    expect(mod.getSession()).toMatchObject({ email: "tab@agency.gov", role: "auditor" });
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

describe("permissions", () => {
  it("keeps auditor read-only and admin fully privileged", () => {
    expect(roleHasPermission("auditor", "view")).toBe(true);
    expect(roleHasPermission("auditor", "dismissAlert")).toBe(false);
    expect(roleHasPermission("auditor", "ingestTransactions")).toBe(false);
    expect(roleHasPermission("analyst", "dismissAlert")).toBe(true);
    expect(roleHasPermission("admin", "manageAdmin")).toBe(true);
  });

  it("checks permissions from the current session shape", () => {
    expect(hasPermission(null, "view")).toBe(false);
    signIn(DEMO_ROLES[1].email, false, DEMO_ROLES[1].role);
    expect(hasPermission(getSession(), "reviewSar")).toBe(true);
    expect(hasPermission(getSession(), "manageAdmin")).toBe(false);
  });
});
