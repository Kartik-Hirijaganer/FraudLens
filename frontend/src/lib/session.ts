/**
 * Summary: The signed-in identity and demo-session store for the app (plan §16 Phase 11,
 * RBAC hardening Phase 1 / Track B). `DEMO_ROLES` are synthetic portfolio/demo personas mapped
 * to canonical backend roles, and `signIn` persists the selected role so local-demo API calls can
 * send the dev-only role header. Real Supabase auth passes an access token while preserving the
 * same `Session` shape for shell, API, SSE, and permission helpers.
 *
 * Key classes:
 * - Analyst: display identity for the shell and dashboard.
 * - DemoRole: selectable demo persona plus its canonical backend role.
 * - Session: authenticated session state (email, role, display identity, optional token).
 *
 * Key functions:
 * - DEMO_ROLES: portfolio/demo personas offered by the login picker.
 * - currentAnalyst: fallback display identity when a non-demo email signs in.
 * - DEMO_ROLE_HEADER:
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
 * - Demo credentials are synthetic (no PHI, no real secret). The client-sent demo role is honored
 * only by the backend's non-prod dev bypass; production auth ignores it and uses verified JWTs.
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

export interface DemoRole {
  id: string;
  role: UserRole;
  name: string;
  tag: string;
  accent: RoleAccent;
  analyst: Analyst;
  email: string;
  legacyEmails?: readonly string[];
  demoPassword: string;
}

export interface Session {
  email: string;
  role: UserRole;
  analyst: Analyst;
  demoRole?: UserRole;
  accessToken?: string;
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

// Synthetic, non-secret demo passphrase shared by every demo role. Displayed and
// auto-filled purely client-side for the personal demo build -- never a real credential.
const DEMO_PASSWORD = "demo-access-2026";

export const DEMO_ROLES: readonly DemoRole[] = [
  {
    id: "analyst",
    role: "analyst",
    name: "Fraud Analyst",
    tag: "Queue",
    accent: "green",
    analyst: { name: "Alex Rivera", initials: "AR" },
    email: "analyst@demo-agency.test",
    legacyEmails: ["analyst@fraudlens.demo"],
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "reviewer",
    role: "reviewer",
    name: "Reviewer",
    tag: "Approve",
    accent: "cyan",
    analyst: { name: "Morgan Diaz", initials: "MD" },
    email: "reviewer@demo-agency.test",
    legacyEmails: ["senior@fraudlens.demo"],
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "admin",
    role: "admin",
    name: "Compliance Admin",
    tag: "Model",
    accent: "amber",
    analyst: { name: "Priya Shah", initials: "PS" },
    email: "admin@demo-agency.test",
    legacyEmails: ["admin@fraudlens.demo"],
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "auditor",
    role: "auditor",
    name: "Auditor",
    tag: "Read-only",
    accent: "slate",
    analyst: { name: "Jordan Lee", initials: "JL" },
    email: "auditor@demo-agency.test",
    legacyEmails: ["auditor@fraudlens.demo"],
    demoPassword: DEMO_PASSWORD,
  },
];

export const currentAnalyst: Analyst = {
  name: "Alex",
  initials: "AR",
};

const STORAGE_KEY = "fraudlens.session";
export const DEMO_ROLE_HEADER = "X-FraudLens-Demo-Role";

function isUserRole(value: unknown): value is UserRole {
  return typeof value === "string" && USER_ROLES.includes(value as UserRole);
}

function demoRoleByEmail(email: string): DemoRole | undefined {
  const normalized = email.toLowerCase();
  return DEMO_ROLES.find(
    (role) =>
      role.email.toLowerCase() === normalized ||
      role.legacyEmails?.some((legacyEmail) => legacyEmail.toLowerCase() === normalized),
  );
}

function demoRoleByRole(role: UserRole): DemoRole | undefined {
  return DEMO_ROLES.find((demoRole) => demoRole.role === role);
}

function buildSession(email: string, role?: UserRole, accessToken?: string): Session {
  const demoRole = role ? demoRoleByRole(role) : demoRoleByEmail(email);
  const resolvedRole = demoRole?.role ?? role ?? "analyst";
  return {
    email,
    role: resolvedRole,
    analyst: demoRole?.analyst ?? currentAnalyst,
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
      };
      if (typeof parsed.email === "string" && parsed.email.length > 0) {
        const role = isUserRole(parsed.role) ? parsed.role : undefined;
        const accessToken =
          typeof parsed.accessToken === "string" && parsed.accessToken.length > 0
            ? parsed.accessToken
            : undefined;
        return buildSession(parsed.email, role, accessToken);
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
): Session {
  currentSession = buildSession(email, role, accessToken);
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
  };
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
