/**
 * Summary: Vite build/dev configuration for the FraudLens frontend. Registers the
 * React plugin (Fast Refresh + JSX transform). Test configuration lives separately
 * in vitest.config.ts.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - default: the resolved Vite config.
 *
 * Notes:
 * - The dev server proxy / API base URL is supplied via VITE_* env, not hardcoded.
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
});
