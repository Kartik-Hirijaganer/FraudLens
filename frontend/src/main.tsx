/**
 * Summary: Browser entry point. Loads the self-hosted fonts (Inter for body,
 * Manrope for heavy display text — the open-source stand-in for Wise Sans),
 * the Tailwind stylesheet, and mounts <App/> into #root under StrictMode.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - (none)
 *
 * Notes:
 * - Excluded from coverage (bootstrap glue); behavior is covered via App tests.
 * - Installs the global client-error reporter so uncaught errors reach the gateway sink.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";

import { App } from "./App";
import { installErrorReporter } from "./lib/logger";
import { updateAccessToken } from "./lib/session";
import { subscribeToSupabaseAuth } from "./lib/supabase";

import "./index.css";

installErrorReporter();
subscribeToSupabaseAuth(updateAccessToken);

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
