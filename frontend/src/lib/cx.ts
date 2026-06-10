/**
 * Summary: Tiny class-name combiner. Joins the truthy string arguments with a
 * single space and drops false/null/undefined, so components can compose Tailwind
 * classes (base + variant + caller-supplied className) without duplicating the
 * filter/join logic (rule 5: no duplication).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - cx: join truthy class-name parts into one string.
 *
 * Notes:
 * - Intentionally dependency-free (no clsx) for such a small helper.
 */
export type ClassValue = string | false | null | undefined;

export function cx(...parts: ClassValue[]): string {
  return parts.filter(Boolean).join(" ");
}
