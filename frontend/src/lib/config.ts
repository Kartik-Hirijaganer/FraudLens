/**
 * Summary: Frontend runtime configuration sourced from Vite `VITE_*` env vars —
 * never hardcoded URLs (rule 4). readConfig is a pure function over an env object
 * so it is trivially testable; `config` is the resolved singleton for app use.
 *
 * Key classes:
 * - AppConfig: shape of the resolved configuration.
 *
 * Key functions:
 * - readConfig: derive an AppConfig from an import-meta env object.
 * - config: the resolved configuration for this build.
 *
 * Notes:
 * - apiBaseUrl defaults to "" (same-origin relative requests) when unset.
 */
export interface AppConfig {
  apiBaseUrl: string;
  appVersion: string;
}

export function readConfig(env: ImportMetaEnv): AppConfig {
  return {
    apiBaseUrl: env.VITE_API_BASE_URL ?? "",
    appVersion: env.VITE_APP_VERSION ?? "dev",
  };
}

export const config: AppConfig = readConfig(import.meta.env);
