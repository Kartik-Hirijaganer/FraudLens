/**
 * Summary: The analyst identity and sign-in session for the app (plan §16 Phase 11).
 * FraudLens has no server auth yet, so this is a clearly-labelled pre-auth layer living
 * in one place (rule 5: no duplication): `DEMO_ROLES` are the synthetic demo identities
 * the login screen can auto-fill, and a tiny storage-backed store (`signIn` / `signOut` /
 * `useSession`) gates the shell. When real JWT auth lands, the store is replaced by the
 * token-derived identity and every consumer (`useSession`, `currentAnalyst`) keeps working.
 *
 * Key classes:
 * - Analyst: the display identity of an analyst (name + avatar initials).
 * - DemoRole: a selectable demo role plus the synthetic credentials it auto-fills.
 * - Session: the authenticated session state (the signed-in email).
 *
 * Key functions:
 * - DEMO_ROLES: the demo roles offered by the login "Sign in as" dropdown.
 * - currentAnalyst: the placeholder analyst rendered by the shell until JWT auth lands.
 * - signIn: start a session (persisted per the "keep signed in" choice).
 * - signOut: clear the session from memory and both storages.
 * - getSession: read the current session synchronously (null when signed out).
 * - useSession: subscribe to the session so a component re-renders on change.
 *
 * Notes:
 * - `DEMO_ROLES` credentials are synthetic demo values (no PHI, no real secret) — the whole
 *   point of the demo dropdown is that they are shown and auto-filled client-side.
 * - "Keep me signed in" persists to localStorage; otherwise the session lives in
 *   sessionStorage and is dropped when the tab closes.
 */
import { useSyncExternalStore } from "react";

export interface Analyst {
  name: string;
  initials: string;
}

// The accent that colours a role's status dot in the login picker.
export type RoleAccent = "green" | "cyan" | "amber" | "slate";

export interface DemoRole {
  id: string;
  name: string;
  tag: string;
  accent: RoleAccent;
  analyst: Analyst;
  email: string;
  demoPassword: string;
}

// Synthetic, non-secret demo passphrase shared by every demo role. Displayed and
// auto-filled purely client-side for the personal demo build — never a real credential.
const DEMO_PASSWORD = "demo-access-2026";

export const DEMO_ROLES: readonly DemoRole[] = [
  {
    id: "analyst",
    name: "Fraud Analyst",
    tag: "Queue",
    accent: "green",
    analyst: { name: "Alex Rivera", initials: "AR" },
    email: "analyst@fraudlens.demo",
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "senior",
    name: "Senior Analyst",
    tag: "Approve",
    accent: "cyan",
    analyst: { name: "Morgan Diaz", initials: "MD" },
    email: "senior@fraudlens.demo",
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "admin",
    name: "Compliance Admin",
    tag: "Model",
    accent: "amber",
    analyst: { name: "Priya Shah", initials: "PS" },
    email: "admin@fraudlens.demo",
    demoPassword: DEMO_PASSWORD,
  },
  {
    id: "auditor",
    name: "Auditor",
    tag: "Read-only",
    accent: "slate",
    analyst: { name: "Jordan Lee", initials: "JL" },
    email: "auditor@fraudlens.demo",
    demoPassword: DEMO_PASSWORD,
  },
];

export const currentAnalyst: Analyst = {
  name: "Alex",
  initials: "AR",
};

export interface Session {
  email: string;
}

const STORAGE_KEY = "fraudlens.session";

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
      const parsed = JSON.parse(raw) as { email?: unknown };
      if (typeof parsed.email === "string" && parsed.email.length > 0) {
        return { email: parsed.email };
      }
    } catch {
      // Malformed entry — ignore and fall through to a signed-out state.
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

export function signIn(email: string, remember = false): Session {
  currentSession = { email };
  const target = safeStorage(remember ? window.localStorage : window.sessionStorage);
  const other = safeStorage(remember ? window.sessionStorage : window.localStorage);
  target?.setItem(STORAGE_KEY, JSON.stringify(currentSession));
  other?.removeItem(STORAGE_KEY);
  emit();
  return currentSession;
}

export function signOut(): void {
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
