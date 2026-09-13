/**
 * Summary: Environment gates for local bypass and real portfolio-demo authentication.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - isDemoPickerEnabled: enable either supported demo login mode.
 * - isDemoBypassEnabled: gate the local-only tokenless bypass.
 * - isLiveDemoAuthEnabled: gate real Supabase demo authentication.
 *
 * Notes:
 * - The development bypass requires both DEV and its explicit flag.
 */
export type LoginEnv = Pick<
  ImportMetaEnv,
  "DEV" | "VITE_AUTH_DEV_BYPASS" | "VITE_DEMO_AUTH_ENABLED"
>;

export function isDemoPickerEnabled(env: LoginEnv = import.meta.env): boolean {
  return isDemoBypassEnabled(env) || isLiveDemoAuthEnabled(env);
}

export function isDemoBypassEnabled(
  env: Pick<ImportMetaEnv, "DEV" | "VITE_AUTH_DEV_BYPASS"> = import.meta.env,
): boolean {
  return env.DEV && env.VITE_AUTH_DEV_BYPASS === "true";
}

// Live demo auth = real Supabase email/password sign-in against the seeded demo tenant. It is
// the picker gate for portfolio production builds, where the tokenless dev bypass is off.
export function isLiveDemoAuthEnabled(
  env: Pick<ImportMetaEnv, "VITE_DEMO_AUTH_ENABLED"> = import.meta.env,
): boolean {
  return env.VITE_DEMO_AUTH_ENABLED === "true";
}
