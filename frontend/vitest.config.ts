/**
 * Summary: Vitest configuration — jsdom environment, global test APIs, the
 * Testing-Library setup file, and v8 coverage with 90% thresholds on lines,
 * functions, branches, and statements (matching the backend's ≥90% bar).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - default: the resolved Vitest config.
 *
 * Notes:
 * - Coverage measures src/ application code; bootstrap (main.tsx), the env shim,
 *   and test files themselves are excluded.
 */
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    exclude: [...configDefaults.exclude, "**/_template.test.tsx"],
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx", "src/vite-env.d.ts"],
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
      },
    },
  },
});
