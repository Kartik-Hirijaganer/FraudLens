/**
 * Summary: The signed-in identity and demo-session store for the app (plan §16 Phase 11,
 * RBAC hardening Phase 1 / Track B; GFP study Phase 7). `DEMO_ROLES` are synthetic portfolio/demo
 * personas — each bound to a specific demo AGENCY (`DEMO_AGENCIES`) as well as a canonical backend
 * role — offered by the login picker; `signIn` persists the selected persona so local-demo API
 * calls can send the dev-only role header. Real Supabase auth passes an access token and the
 * verified `/me` agency id, preserving the same `Session` shape for shell, API, SSE, and permission
 * helpers. Personas resolve by EMAIL first (the picker's unique key) so two personas that share a
 * role — e.g. the Agency One and Agency Two analysts — never mis-resolve to whichever comes first.
 *
 * Key classes:
 * - Analyst: display identity for the shell and dashboard.
 * - DemoAgency: a synthetic demo tenant (id + visual-study index + display name).
 * - DemoRole: a selectable demo persona (canonical role + owning agency + live-auth gate).
 * - Session: authenticated session state (email, role, agency, display identity, optional token).
 *
 * Key functions:
 * - DEMO_AGENCIES: the synthetic demo tenants, indexed to match the study visual data.
 * - DEMO_ROLES: portfolio/demo personas offered by the login picker.
 * - demoAgencyById: resolve a demo agency by its tenant id (null when unknown).
 * - currentAnalyst: fallback display identity when a non-demo email signs in.
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
 * - Demo credentials are synthetic (no PHI, no real secret). The client-sent demo role is honored
 * only by the backend's non-prod dev bypass; production auth ignores it and uses verified JWTs.
 * - The agency id is never sent as a client header (there is no client-selectable tenant); it is
 * read back from the verified `/me` response and only persisted for display + the research view.
 * - `DEMO_AGENCIES` ids mirror the backend `AML_DEMO_AGENCIES` fixed UUIDs so a verified `/me`
 * agency id resolves to the same synthetic tenant the study visual data is indexed by.
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

export interface DemoAgency {
  // Fixed synthetic tenant id — mirrors the backend `AML_DEMO_AGENCIES` UUIDs so a verified
  // `/me` agency id resolves to the tenant the study visual data is indexed by.
  id: string;
  // Agency index used by the study visual data (node/edge `agencyIndex`).
  index: number;
  name: string;
}

// Synthetic demo tenants (ids mirror backend `AML_DEMO_AGENCIES`). Agency One is the seeded
// dev-bypass tenant; Agency Two/Three exist only as real Supabase-authenticated demo tenants.
export const DEMO_AGENCIES: readonly DemoAgency[] = [
  { id: "11111111-1111-4111-8111-111111111111", index: 0, name: "Demo Financial Agency" },
  { id: "11111111-1111-4111-8111-111111111112", index: 1, name: "AML Demo Agency Two" },
  { id: "11111111-1111-4111-8111-111111111113", index: 2, name: "AML Demo Agency Three" },
];

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
  // The synthetic tenant this persona belongs to.
  agencyId: string;
  // True when the persona can only sign in through real Supabase auth (an agency-bound JWT):
  // the tokenless dev bypass mints Agency One claims only, so non-Agency-One personas need it.
  requiresLiveAuth?: boolean;
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

// Synthetic, non-secret demo passphrase shared by every demo role. Displayed and
// auto-filled purely client-side for the personal demo build -- never a real credential.
const DEMO_PASSWORD = "demo-access-2026";

const AGENCY_ONE_ID = DEMO_AGENCIES[0].id;
const AGENCY_TWO_ID = DEMO_AGENCIES[1].id;

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
    agencyId: AGENCY_ONE_ID,
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
    agencyId: AGENCY_ONE_ID,
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
    agencyId: AGENCY_ONE_ID,
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
    agencyId: AGENCY_ONE_ID,
  },
  {
    // Agency Two analyst — the second isolated tenant. Only usable through real Supabase auth
    // (an agency-bound JWT); the dev bypass mints Agency One, so this persona is hidden unless
    // live demo auth is enabled. It makes the tenant-isolation study's two perspectives feelable.
    id: "analyst-agency-two",
    role: "analyst",
    name: "Fraud Analyst · Agency Two",
    tag: "Agency Two",
    accent: "cyan",
    analyst: { name: "Sam Okafor", initials: "SO" },
    email: "analyst@aml-demo-agency-two.test",
    demoPassword: DEMO_PASSWORD,
    agencyId: AGENCY_TWO_ID,
    requiresLiveAuth: true,
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

export function demoAgencyById(agencyId: string | undefined): DemoAgency | null {
  if (!agencyId) {
    return null;
  }
  return DEMO_AGENCIES.find((agency) => agency.id === agencyId) ?? null;
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

function buildSession(
  email: string,
  role?: UserRole,
  accessToken?: string,
  agencyId?: string,
): Session {
  // Resolve by email first (the picker's unique key) so two personas that share a role — the
  // Agency One and Agency Two analysts — never mis-resolve to whichever is declared first.
  const demoRole = demoRoleByEmail(email) ?? (role ? demoRoleByRole(role) : undefined);
  const resolvedRole = demoRole?.role ?? role ?? "analyst";
  const resolvedAgencyId = agencyId ?? demoRole?.agencyId;
  return {
    email,
    role: resolvedRole,
    analyst: demoRole?.analyst ?? currentAnalyst,
    ...(resolvedAgencyId ? { agencyId: resolvedAgencyId } : {}),
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
        agencyId?: unknown;
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
        return buildSession(parsed.email, role, accessToken, agencyId);
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
): Session {
  currentSession = buildSession(email, role, accessToken, agencyId);
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
