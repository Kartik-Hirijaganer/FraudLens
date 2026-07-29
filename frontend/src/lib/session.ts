/**
 * Summary: The signed-in identity and demo-session store for the app (plan §16 Phase 11,
 * RBAC hardening Phase 1 / Track B; GFP study Phase 7). It holds NO demo identity data: the
 * login picker's personas are supplied by the backend's safe public projection of
 * `config/portfolio-demo.yaml`, typed here as `DemoRole`, and `signIn` persists the selected
 * role so local-demo API calls can send the dev-only role header. Real Supabase auth passes an
 * access token and the verified `/me` agency id, preserving the same `Session` shape for shell,
 * API, SSE, and permission helpers. The DISPLAY identity is supplied by the caller too — the
 * picked persona's configured name/initials, or the verified `/me` display name — and is
 * persisted with the session, so a reload restores it without any identity constant in source.
 *
 * Key classes:
 * - Analyst: display identity for the shell and dashboard.
 * - DemoRole: a selectable demo persona supplied by the backend projection.
 * - Session: authenticated session state (email, role, agency, display identity, optional token).
 *
 * Key functions:
 * - analystFromDisplayName: build a display identity from a backend-supplied display name.
 * - DEMO_ROLE_HEADER: the dev-only header name the local-demo bypass honors.
 * - roleHasPermission: role-level permission helper used for UX gating.
 * - hasPermission: session-level permission helper used by the shell.
 * - withSessionHeaders: attach bearer/demo auth to REST and SSE requests.
 * - signIn: start a session (persisted per the "keep signed in" choice).
 * - updateAccessToken: replace the stored bearer token after Supabase refresh.
 * - signOut: clear the session from memory/storage and sign out of Supabase when configured.
 * - getSession: read the current session synchronously (null when signed out).
 * - useSession: subscribe to the session so a component re-renders on change.
 *
 * Notes:
 * - Demo credentials are synthetic (no PHI, no real secret) and never declared here. The
 * client-sent demo role is honored only by the backend's non-prod dev bypass; production auth
 * ignores it and uses verified JWTs.
 * - The agency id is never sent as a client header (there is no client-selectable tenant); it is
 * read back from the verified `/me` response and only persisted for display.
 * - Exactly one persistent demo tenant exists, so there is no client-side tenant table.
 * - With no display identity supplied (a rehydrated pre-projection entry), one is DERIVED from
 * the session email — generic string handling, never a named identity.
 */
import { useSyncExternalStore } from "react";

import { signOutSupabase } from "./supabase";

export type UserRole = "auditor" | "analyst" | "reviewer" | "admin";

export type SessionPermission =
  | "view"
  | "ingestTransactions"
  | "startInvestigation"
  | "triageAlert"
  | "dismissAlert"
  | "finalizeAlert"
  | "reviewSar"
  | "manageRules"
  | "manageAdmin";

export interface Analyst {
  name: string;
  initials: string;
}

// The accent that colours a role's status dot in the login picker.
export type RoleAccent = "green" | "cyan" | "amber" | "slate";

// One selectable demo persona. Every value is supplied by the backend's public projection of
// `config/portfolio-demo.yaml` — never declared in TypeScript.
export interface DemoRole {
  id: string;
  role: UserRole;
  name: string;
  tag: string;
  accent: RoleAccent;
  analyst: Analyst;
  email: string;
  demoPassword: string;
  // The synthetic tenant this persona belongs to (the one runtime demo agency).
  agencyId: string;
}

export interface Session {
  email: string;
  role: UserRole;
  analyst: Analyst;
  demoRole?: UserRole;
  accessToken?: string;
  // The signed-in tenant (from the persona for a demo session, or verified `/me` for live auth).
  agencyId?: string;
}

const USER_ROLES: readonly UserRole[] = ["auditor", "analyst", "reviewer", "admin"];

const ROLE_PERMISSIONS: Record<UserRole, readonly SessionPermission[]> = {
  auditor: ["view"],
  analyst: ["view", "ingestTransactions", "startInvestigation", "triageAlert", "dismissAlert"],
  reviewer: [
    "view",
    "ingestTransactions",
    "startInvestigation",
    "triageAlert",
    "dismissAlert",
    "finalizeAlert",
    "reviewSar",
  ],
  admin: [
    "view",
    "ingestTransactions",
    "startInvestigation",
    "triageAlert",
    "dismissAlert",
    "finalizeAlert",
    "reviewSar",
    "manageRules",
    "manageAdmin",
  ],
};

const STORAGE_KEY = "fraudlens.session";
export const DEMO_ROLE_HEADER = "X-FraudLens-Demo-Role";
const MAX_INITIALS = 2;

function isUserRole(value: unknown): value is UserRole {
  return typeof value === "string" && USER_ROLES.includes(value as UserRole);
}

// Initials from any display name: the first letter of up to the first two words, falling back
// to the first character so a single-token name still yields an avatar.
function initialsFrom(name: string): string {
  const words = name.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const letters = words
    .slice(0, MAX_INITIALS)
    .map((word) => word.slice(0, 1).toUpperCase())
    .join("");
  return letters || name.slice(0, 1).toUpperCase();
}

export function analystFromDisplayName(displayName: string): Analyst {
  const name = displayName.trim();
  return { name, initials: initialsFrom(name) };
}

// Last resort when no display identity was supplied: title-case the email's local part. Pure
// string handling — it invents no name that is not already in the session's own email.
function analystFromEmail(email: string): Analyst {
  const local = email.split("@")[0] ?? "";
  const name = local
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean)
    .map((word) => word.slice(0, 1).toUpperCase() + word.slice(1))
    .join(" ");
  return analystFromDisplayName(name || email);
}

function buildSession(
  email: string,
  role?: UserRole,
  accessToken?: string,
  agencyId?: string,
  analyst?: Analyst,
): Session {
  // The role is the caller's (a picked persona, or the verified `/me` claim); there is no
  // client-side persona table to resolve against.
  const resolvedRole = role ?? "analyst";
  return {
    email,
    role: resolvedRole,
    analyst: analyst ?? analystFromEmail(email),
    ...(agencyId ? { agencyId } : {}),
    ...(accessToken ? { accessToken } : { demoRole: resolvedRole }),
  };
}

export function roleHasPermission(role: UserRole, permission: SessionPermission): boolean {
  return ROLE_PERMISSIONS[role].includes(permission);
}

export function hasPermission(
  session: Pick<Session, "role"> | null,
  permission: SessionPermission,
): boolean {
  return session !== null && roleHasPermission(session.role, permission);
}

function safeStorage(store: Storage): Storage | null {
  try {
    // Touching storage can throw (privacy mode / disabled); probe once.
    store.getItem(STORAGE_KEY);
    return store;
  } catch {
    return null;
  }
}

// The persisted display identity, or undefined when the stored entry predates it (or is
// malformed) so the caller falls back to deriving one.
function readAnalyst(value: unknown): Analyst | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const { name, initials } = value as { name?: unknown; initials?: unknown };
  if (typeof name !== "string" || name.length === 0) {
    return undefined;
  }
  return {
    name,
    initials: typeof initials === "string" && initials.length > 0 ? initials : initialsFrom(name),
  };
}

function readStoredSession(): Session | null {
  for (const store of [window.localStorage, window.sessionStorage]) {
    const safe = safeStorage(store);
    if (!safe) {
      continue;
    }
    const raw = safe.getItem(STORAGE_KEY);
    if (!raw) {
      continue;
    }
    try {
      const parsed = JSON.parse(raw) as {
        email?: unknown;
        role?: unknown;
        accessToken?: unknown;
        agencyId?: unknown;
        analyst?: unknown;
      };
      if (typeof parsed.email === "string" && parsed.email.length > 0) {
        const role = isUserRole(parsed.role) ? parsed.role : undefined;
        const accessToken =
          typeof parsed.accessToken === "string" && parsed.accessToken.length > 0
            ? parsed.accessToken
            : undefined;
        const agencyId =
          typeof parsed.agencyId === "string" && parsed.agencyId.length > 0
            ? parsed.agencyId
            : undefined;
        return buildSession(parsed.email, role, accessToken, agencyId, readAnalyst(parsed.analyst));
      }
    } catch {
      // Malformed entry -- ignore and fall through to a signed-out state.
    }
  }
  return null;
}

// Cached snapshot so useSyncExternalStore sees a stable reference between changes.
let currentSession: Session | null = readStoredSession();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

function storageContainingSession(): Storage | null {
  for (const store of [window.localStorage, window.sessionStorage]) {
    const safe = safeStorage(store);
    if (safe?.getItem(STORAGE_KEY)) {
      return safe;
    }
  }
  return safeStorage(window.sessionStorage);
}

function headersToRecord(headers?: HeadersInit): Record<string, string> {
  if (!headers) {
    return {};
  }
  if (headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }
  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }
  return { ...headers };
}

export function withSessionHeaders(init?: RequestInit): RequestInit | undefined {
  const session = getSession();
  if (!session) {
    return init;
  }
  const headers = headersToRecord(init?.headers);
  if (session.accessToken && !("Authorization" in headers)) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  if (session.demoRole && !(DEMO_ROLE_HEADER in headers)) {
    headers[DEMO_ROLE_HEADER] = session.demoRole;
  }
  return { ...init, headers };
}

export function signIn(
  email: string,
  remember = false,
  role?: UserRole,
  accessToken?: string,
  agencyId?: string,
  analyst?: Analyst,
): Session {
  currentSession = buildSession(email, role, accessToken, agencyId, analyst);
  const target = safeStorage(remember ? window.localStorage : window.sessionStorage);
  const other = safeStorage(remember ? window.sessionStorage : window.localStorage);
  target?.setItem(STORAGE_KEY, JSON.stringify(currentSession));
  other?.removeItem(STORAGE_KEY);
  emit();
  return currentSession;
}

export function updateAccessToken(accessToken: string): Session | null {
  if (!currentSession) {
    return null;
  }
  currentSession = {
    email: currentSession.email,
    role: currentSession.role,
    analyst: currentSession.analyst,
    accessToken,
    ...(currentSession.agencyId ? { agencyId: currentSession.agencyId } : {}),
  };
  // The display identity rides through a refresh, so the shell keeps greeting the same person.
  storageContainingSession()?.setItem(STORAGE_KEY, JSON.stringify(currentSession));
  emit();
  return currentSession;
}

export function signOut(): void {
  signOutSupabase();
  currentSession = null;
  safeStorage(window.localStorage)?.removeItem(STORAGE_KEY);
  safeStorage(window.sessionStorage)?.removeItem(STORAGE_KEY);
  emit();
}

export function getSession(): Session | null {
  return currentSession;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession, getSession);
}
