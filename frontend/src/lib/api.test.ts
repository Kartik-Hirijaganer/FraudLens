import { describe, expect, it, vi } from "vitest";

import { ApiError, fetchApiHealth, type ApiHealth } from "./api";

const HEALTH: ApiHealth = {
  status: "ok",
  service: "FraudLens",
  version: "0.1.0",
  environment: "dev",
};

function fakeResponse(ok: boolean, status: number, body: unknown): Response {
  return { ok, status, json: () => Promise.resolve(body) } as unknown as Response;
}

describe("fetchApiHealth", () => {
  it("requests the health endpoint and returns the parsed body", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, 200, HEALTH)));
    const result = await fetchApiHealth(fetchMock);
    expect(result).toEqual(HEALTH);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health");
  });

  it("throws ApiError (with the status) on a non-ok response", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(false, 503, null)));
    await expect(fetchApiHealth(fetchMock)).rejects.toBeInstanceOf(ApiError);
  });
});
