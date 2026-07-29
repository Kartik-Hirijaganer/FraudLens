/**
 * Summary: Loads the backend's safe public projection of `config/portfolio-demo.yaml` and maps
 * it onto the login picker's `DemoRole` shape (plan Phase 3a). This is the ONLY place the demo
 * personas enter the frontend: nothing here declares an agency id, email, display name, or
 * password — every value arrives from `GET /api/v1/portfolio-demo/config`. The hook is gated by
 * the caller, so a build with no demo picker never issues the request, and it reports a
 * four-state status so the login screen can show loading / unavailable without ever blocking
 * ordinary email-password sign-in.
 *
 * Key classes:
 * - PortfolioDemoPersonas: the hook's return shape — a `PortfolioDemoStatus`
 *   (disabled | loading | ready | failed) plus the mapped personas.
 *
 * Key functions:
 * - toDemoRoles: map the backend projection onto the picker's persona list.
 * - usePortfolioDemoPersonas: fetch the projection when enabled and report its status.
 *
 * Notes:
 * - Fetch/race/unmount handling is delegated to the shared `useAsync` hook (rule 5), and the
 *   request is deliberately tokenless — the screen calling it has no session yet.
 * - The password in the projection is public synthetic demo data by design; it is carried onto
 *   the persona so the picker auto-fills it, and is empty when the backend has none configured.
 * - `pickerAccent` is already constrained to the code-owned accent tokens by the API contract,
 *   so no client-side palette mapping (or fallback colour) is needed here.
 */
import { useCallback } from "react";

import { fetchPortfolioDemoConfig, type PortfolioDemoConfig } from "./api";
import type { DemoRole } from "./session";
import { useAsync } from "./useAsync";

export type PortfolioDemoStatus = "disabled" | "loading" | "ready" | "failed";

export interface PortfolioDemoPersonas {
  status: PortfolioDemoStatus;
  personas: readonly DemoRole[];
}

const NO_PERSONAS: readonly DemoRole[] = [];

export function toDemoRoles(config: PortfolioDemoConfig): readonly DemoRole[] {
  return config.personas.map((persona) => ({
    id: persona.key,
    role: persona.role,
    name: persona.pickerName,
    tag: persona.pickerTag,
    accent: persona.pickerAccent,
    analyst: { name: persona.displayName, initials: persona.initials },
    email: persona.email,
    demoPassword: config.syntheticPassword,
    agencyId: config.agency.id,
  }));
}

export function usePortfolioDemoPersonas(
  enabled: boolean,
  load: () => Promise<PortfolioDemoConfig> = fetchPortfolioDemoConfig,
): PortfolioDemoPersonas {
  const loadPersonas = useCallback(
    async (): Promise<readonly DemoRole[]> => (enabled ? toDemoRoles(await load()) : NO_PERSONAS),
    [enabled, load],
  );
  const state = useAsync(loadPersonas, [enabled]);
  if (!enabled) {
    return { status: "disabled", personas: NO_PERSONAS };
  }
  if (state.error !== null) {
    return { status: "failed", personas: NO_PERSONAS };
  }
  if (state.loading || state.data === null) {
    return { status: "loading", personas: NO_PERSONAS };
  }
  return { status: "ready", personas: state.data };
}
