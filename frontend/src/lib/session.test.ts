import { describe, expect, it } from "vitest";

import { currentAnalyst } from "./session";

describe("currentAnalyst", () => {
  it("exposes a display name and non-empty avatar initials", () => {
    expect(currentAnalyst.name).toBeTruthy();
    expect(currentAnalyst.initials).toMatch(/^[A-Z]+$/);
  });
});
