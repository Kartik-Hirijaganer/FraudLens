/**
 * Summary: The signed-in analyst identity surfaced by the app shell (avatar + initials)
 * and the dashboard greeting. FraudLens has no auth layer yet, so this is a single,
 * clearly-labelled pre-auth placeholder living in one place (rule 5: no duplication) —
 * when real authentication lands, it is replaced by the JWT-derived identity and every
 * consumer keeps working unchanged.
 *
 * Key classes:
 * - Analyst: the display identity of the current analyst (name + avatar initials).
 *
 * Key functions:
 * - currentAnalyst: the placeholder analyst rendered until auth is wired.
 *
 * Notes:
 * - Placeholder only — it carries no PHI and no credentials; the real identity will come
 *   from the authenticated session, never a client-supplied value.
 */
export interface Analyst {
  name: string;
  initials: string;
}

export const currentAnalyst: Analyst = {
  name: "Alex",
  initials: "AR",
};
