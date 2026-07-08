/**
 * Summary: Supabase Auth client wiring for Track B real login. The module creates a
 * singleton supabase-js client only when the public Vite Supabase URL and anon key are
 * configured, then exposes login, refresh, auth-state subscription, and sign-out helpers
 * used by Login, the API 401 retry path, and app bootstrap.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - getSupabaseClient: lazily construct the configured Supabase client.
 * - signInWithPassword: email/password login returning the access token.
 * - refreshAccessToken: silent refresh and session-store token update.
 * - subscribeToSupabaseAuth: surface refreshed bearer tokens to the session store.
 * - signOutSupabase: sign out of Supabase without changing the local store directly.
 *
 * Notes:
 * - The anon key is publishable by design. The service-role key is never present in the SPA.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { config } from "./config";

let client: SupabaseClient | null | undefined;

export function getSupabaseClient(): SupabaseClient | null {
  if (client !== undefined) {
    return client;
  }
  if (!config.supabaseUrl || !config.supabaseAnonKey) {
    client = null;
    return client;
  }
  client = createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: true,
    },
  });
  return client;
}

export async function signInWithPassword(email: string, password: string): Promise<string> {
  const supabase = getSupabaseClient();
  if (!supabase) {
    throw new Error("Supabase login is not configured.");
  }
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error || !data.session?.access_token) {
    throw new Error(error?.message ?? "Sign-in failed.");
  }
  return data.session.access_token;
}

export async function refreshAccessToken(): Promise<string | null> {
  const supabase = getSupabaseClient();
  if (!supabase) {
    return null;
  }
  const { data, error } = await supabase.auth.refreshSession();
  if (error || !data.session?.access_token) {
    return null;
  }
  return data.session.access_token;
}

export function subscribeToSupabaseAuth(onAccessToken: (accessToken: string) => void): () => void {
  const supabase = getSupabaseClient();
  if (!supabase) {
    return () => undefined;
  }
  const { data } = supabase.auth.onAuthStateChange((_event, session) => {
    if (session?.access_token) {
      onAccessToken(session.access_token);
    }
  });
  return () => data.subscription.unsubscribe();
}

export function signOutSupabase(): void {
  const supabase = getSupabaseClient();
  if (supabase) {
    void supabase.auth.signOut();
  }
}
