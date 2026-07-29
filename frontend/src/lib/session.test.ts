import { afterEach, describe, expect, it, vi } from "vitest";

import {
  analystFromDisplayName,
  getSession,
  hasPermission,
  roleHasPermission,
  signIn,
  signOut,
  updateAccessToken,
  withSessionHeaders,
} from "./session";
import { TEST_DEMO_AGENCY_ID, demoPersona } from "../test/factories";

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

describe("analystFromDisplayName", () => {
  it("derives avatar initials from a backend-supplied display name", () => {
    expect(analystFromDisplayName("Casey Nolan-Reed")).toEqual({
      name: "Casey Nolan-Reed",
      initials: "CN",
    });
  });

  it("falls back to the first character for a single-token name", () => {
    expect(analystFromDisplayName("  casey  ")).toEqual({ name: "casey", initials: "C" });
  });

  it("keeps a name with no letters usable", () => {
    expect(analystFromDisplayName("...")).toEqual({ name: "...", initials: "." });
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

  it("keeps the caller-supplied role for a picked persona", () => {
    const admin = demoPersona("admin");
    signIn(admin.email, false, admin.role);
    expect(getSession()).toMatchObject({
      email: admin.email,
      role: "admin",
      demoRole: "admin",
    });
  });

  it("carries the caller-supplied display identity and persists it for a reload", () => {
    const reviewer = demoPersona("reviewer");
    signIn(reviewer.email, false, reviewer.role, undefined, reviewer.agencyId, reviewer.analyst);
    expect(getSession()?.analyst).toEqual(reviewer.analyst);
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toContain(reviewer.analyst.name);
  });

  it("derives a display identity from the email when none is supplied", () => {
    // No identity constant exists client-side, so the fallback can only use the session's email.
    signIn("dana.quill@agency.gov");
    expect(getSession()?.analyst).toEqual({ name: "Dana Quill", initials: "DQ" });
  });

  it("keeps the display identity across a token refresh", () => {
    const analyst = demoPersona("analyst");
    signIn(analyst.email, false, analyst.role, "token-1", analyst.agencyId, analyst.analyst);
    updateAccessToken("token-2");
    expect(getSession()?.analyst).toEqual(analyst.analyst);
  });

  it("persists the persona's agency for a demo session", () => {
    const analyst = demoPersona("analyst");
    signIn(analyst.email, false, analyst.role, undefined, analyst.agencyId);
    expect(getSession()?.agencyId).toBe(TEST_DEMO_AGENCY_ID);
  });

  it("never infers a role or tenant from the email (no client-side persona table)", () => {
    // Two personas may share a role; the client resolves neither, so an unknown email with an
    // explicit role/tenant is honoured verbatim and nothing is silently mis-selected.
    signIn("someone@agency.gov", false, "reviewer", "token-two", TEST_DEMO_AGENCY_ID);
    expect(getSession()).toMatchObject({
      email: "someone@agency.gov",
      role: "reviewer",
      agencyId: TEST_DEMO_AGENCY_ID,
    });
    signIn("someone@agency.gov");
    expect(getSession()?.role).toBe("analyst");
    expect(getSession()?.agencyId).toBeUndefined();
  });

  it("persists a verified /me agency for a live session and through a token refresh", () => {
    signIn("real@agency.gov", false, "analyst", "token-1", TEST_DEMO_AGENCY_ID);
    expect(getSession()?.agencyId).toBe(TEST_DEMO_AGENCY_ID);
    updateAccessToken("token-2");
    expect(getSession()).toMatchObject({ accessToken: "token-2", agencyId: TEST_DEMO_AGENCY_ID });
    expect(window.sessionStorage.getItem("fraudlens.session")).toContain(TEST_DEMO_AGENCY_ID);
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

  it("updates the stored access token without turning it into a demo session", () => {
    signIn("reviewer@agency.gov", false, "reviewer", "token-1");
    updateAccessToken("token-2");
    expect(getSession()).toMatchObject({ accessToken: "token-2", role: "reviewer" });
    expect(getSession()?.demoRole).toBeUndefined();
    expect(window.sessionStorage.getItem("fraudlens.session")).toContain("token-2");
  });

  it("builds request headers for bearer and demo sessions", () => {
    signIn("reviewer@agency.gov", false, "reviewer", "token-1");
    expect(withSessionHeaders({ headers: { Accept: "application/json" } })?.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer token-1",
    });
    signOut();
    const analyst = demoPersona("analyst");
    signIn(analyst.email, false, analyst.role);
    expect(withSessionHeaders()?.headers).toMatchObject({ "X-FraudLens-Demo-Role": "analyst" });
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

  it("rehydrates the persisted agency id", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ email: "kept@agency.gov", agencyId: TEST_DEMO_AGENCY_ID }),
    );
    const mod = await importFreshSession();
    expect(mod.getSession()?.agencyId).toBe(TEST_DEMO_AGENCY_ID);
  });

  it("rehydrates the persisted display identity", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        email: "kept@agency.gov",
        analyst: { name: "Robin Vale", initials: "RV" },
      }),
    );
    const mod = await importFreshSession();
    expect(mod.getSession()?.analyst).toEqual({ name: "Robin Vale", initials: "RV" });
  });

  it("recomputes initials when the persisted identity has none", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ email: "kept@agency.gov", analyst: { name: "Robin Vale" } }),
    );
    const mod = await importFreshSession();
    expect(mod.getSession()?.analyst).toEqual({ name: "Robin Vale", initials: "RV" });
  });

  it("derives a display identity when the persisted one is unusable", async () => {
    // A pre-projection entry (or a malformed one) still yields a usable shell identity.
    for (const analyst of [undefined, "nope", { name: "" }]) {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ email: "dana.quill@agency.gov", analyst }),
      );
      const mod = await importFreshSession();
      expect(mod.getSession()?.analyst).toEqual({ name: "Dana Quill", initials: "DQ" });
    }
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
    const reviewer = demoPersona("reviewer");
    signIn(reviewer.email, false, reviewer.role);
    expect(hasPermission(getSession(), "reviewSar")).toBe(true);
    expect(hasPermission(getSession(), "manageAdmin")).toBe(false);
  });
});
