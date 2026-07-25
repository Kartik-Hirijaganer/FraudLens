/**
 * Summary: Filesystem paths shared by Vite and Vitest so the frontend can bundle the canonical
 * ADR-017 Markdown file as a static link target without copying or drifting the document.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none)
 *
 * Notes:
 * - The allowlist is restricted to the frontend and ADR directories; it never exposes repo
 *   configuration, secret-bearing files, or the broader workspace through the dev server.
 */
import { fileURLToPath } from "node:url";

export const FRONTEND_ROOT = fileURLToPath(new URL(".", import.meta.url));
export const ADR_ASSET_DIRECTORY = fileURLToPath(
  new URL("../docs/architecture/adr", import.meta.url),
);
