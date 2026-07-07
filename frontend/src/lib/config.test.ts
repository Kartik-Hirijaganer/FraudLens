import { describe, expect, it } from "vitest";

import { readConfig } from "./config";

describe("readConfig", () => {
  it("reads VITE_* values when present", () => {
    const cfg = readConfig({
      VITE_API_BASE_URL: "https://api.example.test",
      VITE_APP_VERSION: "1.2.3",
      VITE_SUPABASE_URL: "https://project.supabase.test",
      VITE_SUPABASE_ANON_KEY: "publishable-key",
    } as ImportMetaEnv);
    expect(cfg.apiBaseUrl).toBe("https://api.example.test");
    expect(cfg.appVersion).toBe("1.2.3");
    expect(cfg.supabaseUrl).toBe("https://project.supabase.test");
    expect(cfg.supabaseAnonKey).toBe("publishable-key");
  });

  it("falls back to safe defaults when unset", () => {
    const cfg = readConfig({} as ImportMetaEnv);
    expect(cfg.apiBaseUrl).toBe("");
    expect(cfg.appVersion).toBe("dev");
    expect(cfg.supabaseUrl).toBe("");
    expect(cfg.supabaseAnonKey).toBe("");
  });
});
