import { describe, expect, it, vi } from "vitest";

import { installErrorReporter, reportClientError, scrubForLog } from "./logger";

function okResponse(): Response {
  return { ok: true, status: 202, json: () => Promise.resolve({}) } as unknown as Response;
}

describe("scrubForLog", () => {
  it("masks emails and long digit runs", () => {
    expect(scrubForLog("acct 4111111111111111 from a@b.com")).toBe(
      "acct [redacted] from [redacted]",
    );
  });

  it("leaves short numbers untouched", () => {
    expect(scrubForLog("code 1234")).toBe("code 1234");
  });
});

describe("reportClientError", () => {
  it("posts a scrubbed message + context to the client-error sink", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(okResponse()));
    await reportClientError(
      "boom 4111111111111111",
      { route: "alerts" },
      { fetchImpl: fetchMock, baseUrl: "" },
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/telemetry/client-error");
    const body = JSON.parse(init.body as string) as { message: string; context: { route: string } };
    expect(body.message).toBe("boom [redacted]");
    expect(body.context.route).toBe("alerts");
  });

  it("swallows a failed post", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error("offline")));
    await expect(
      reportClientError("x", undefined, { fetchImpl: fetchMock, baseUrl: "" }),
    ).resolves.toBeUndefined();
  });
});

describe("installErrorReporter", () => {
  it("forwards window error events to the sink and stops after cleanup", () => {
    const fetchMock = vi.fn(() => Promise.resolve(okResponse()));
    const cleanup = installErrorReporter(window, { fetchImpl: fetchMock, baseUrl: "" });
    window.dispatchEvent(new ErrorEvent("error", { message: "kaboom" }));
    window.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason: "nope" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    cleanup();
    window.dispatchEvent(new ErrorEvent("error", { message: "again" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
