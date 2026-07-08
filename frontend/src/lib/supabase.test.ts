import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  vi.doUnmock("./config");
  vi.doUnmock("@supabase/supabase-js");
});

function mockConfiguredClient() {
  const unsubscribe = vi.fn();
  const auth = {
    signInWithPassword: vi.fn(() =>
      Promise.resolve({ data: { session: { access_token: "token-1" } }, error: null }),
    ),
    refreshSession: vi.fn(() =>
      Promise.resolve({ data: { session: { access_token: "token-2" } }, error: null }),
    ),
    onAuthStateChange: vi.fn(
      (callback: (event: string, session: { access_token: string }) => void) => {
        callback("TOKEN_REFRESHED", { access_token: "token-3" });
        return { data: { subscription: { unsubscribe } } };
      },
    ),
    signOut: vi.fn(() => Promise.resolve({ error: null })),
  };
  const createClient = vi.fn(() => ({ auth }));
  vi.doMock("./config", () => ({
    config: {
      apiBaseUrl: "",
      appVersion: "test",
      supabaseUrl: "https://project.supabase.test",
      supabaseAnonKey: "publishable-key",
    },
  }));
  vi.doMock("@supabase/supabase-js", () => ({ createClient }));
  return { auth, createClient, unsubscribe };
}

describe("Supabase auth helpers", () => {
  it("returns null and no-op helpers when Supabase is not configured", async () => {
    vi.doMock("./config", () => ({
      config: { apiBaseUrl: "", appVersion: "test", supabaseUrl: "", supabaseAnonKey: "" },
    }));
    const mod = await import("./supabase");

    expect(mod.getSupabaseClient()).toBeNull();
    await expect(mod.signInWithPassword("user@example.test", "pw")).rejects.toThrow(
      /not configured/i,
    );
    await expect(mod.refreshAccessToken()).resolves.toBeNull();
    expect(() => mod.subscribeToSupabaseAuth(() => undefined)()).not.toThrow();
    expect(() => mod.signOutSupabase()).not.toThrow();
  });

  it("signs in, refreshes, subscribes, and signs out with the configured client", async () => {
    const { auth, createClient, unsubscribe } = mockConfiguredClient();
    const mod = await import("./supabase");
    const onToken = vi.fn();

    expect(mod.getSupabaseClient()).not.toBeNull();
    const createArgs = createClient.mock.calls[0] as unknown as [
      string,
      string,
      { auth: { autoRefreshToken: boolean; persistSession: boolean } },
    ];
    expect(createArgs[0]).toBe("https://project.supabase.test");
    expect(createArgs[1]).toBe("publishable-key");
    expect(createArgs[2].auth.autoRefreshToken).toBe(true);
    expect(createArgs[2].auth.persistSession).toBe(true);
    await expect(mod.signInWithPassword("user@example.test", "pw")).resolves.toBe("token-1");
    await expect(mod.refreshAccessToken()).resolves.toBe("token-2");
    const cleanup = mod.subscribeToSupabaseAuth(onToken);
    expect(onToken).toHaveBeenCalledWith("token-3");
    cleanup();
    expect(unsubscribe).toHaveBeenCalledOnce();
    mod.signOutSupabase();
    expect(auth.signOut).toHaveBeenCalledOnce();
  });
});
