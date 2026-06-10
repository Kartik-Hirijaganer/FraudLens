import { describe, expect, it } from "vitest";

import { readConfig } from "./config";

describe("readConfig", () => {
  it("reads VITE_* values when present", () => {
    const cfg = readConfig({
      VITE_API_BASE_URL: "https://api.example.test",
      VITE_APP_VERSION: "1.2.3",
    } as ImportMetaEnv);
    expect(cfg.apiBaseUrl).toBe("https://api.example.test");
    expect(cfg.appVersion).toBe("1.2.3");
  });

  it("falls back to safe defaults when unset", () => {
    const cfg = readConfig({} as ImportMetaEnv);
    expect(cfg.apiBaseUrl).toBe("");
    expect(cfg.appVersion).toBe("dev");
  });
});
