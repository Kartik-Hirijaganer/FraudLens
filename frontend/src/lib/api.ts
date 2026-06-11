/**
 * Summary: Typed client for the FraudLens backend. The API surface is camelCase
 * (FraudLens casing), so the response interfaces use camelCase fields directly. fetch
 * is injectable so tests don't touch the network; the base URL comes from config
 * (VITE_*), never a hardcoded host.
 *
 * Key classes:
 * - ApiHealth: shape of GET /api/v1/health.
 * - ApiError: error thrown on a non-2xx response (carries the status code).
 *
 * Key functions:
 * - fetchApiHealth: call GET /api/v1/health and return the parsed body.
 *
 * Notes:
 * - The error message contains only the status code — never response bodies that
 *   could carry sensitive data.
 */
import { config } from "./config";

export interface ApiHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function fetchApiHealth(fetchImpl: typeof fetch = fetch): Promise<ApiHealth> {
  const response = await fetchImpl(`${config.apiBaseUrl}/api/v1/health`);
  if (!response.ok) {
    throw new ApiError(response.status, `health request failed with status ${response.status}`);
  }
  return (await response.json()) as ApiHealth;
}
