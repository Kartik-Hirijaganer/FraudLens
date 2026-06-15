/**
 * Summary: The FraudLens app shell (plan §16 Phase 11). It renders the wise nav-bar +
 * sidebar (DESIGN.md app-shell), switches the main content on the hash route
 * (`useHashRoute`), and mounts the Sonner `<Toaster/>` once so any page can raise a toast.
 * Each page owns its own data + states; the shell only routes and frames them. Surfaces
 * cycle sage canvas → white cards, and the active nav row uses an ink indicator (the brand
 * green stays reserved for primary CTAs per DESIGN.md).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - App: render the shell + route the current page.
 *
 * Notes:
 * - Unknown ids deep-link straight to the relevant page (which resolves existence); an
 *   unrecognized route renders a not-found empty state.
 */
import { Toaster } from "sonner";

import { EmptyState } from "./components/feedback/EmptyState";
import { AlertDetail } from "./pages/AlertDetail";
import { Alerts } from "./pages/Alerts";
import { Dashboard } from "./pages/Dashboard";
import { Investigation } from "./pages/Investigation";
import { ModelAdmin } from "./pages/ModelAdmin";
import { Transactions } from "./pages/Transactions";
import { cx } from "./lib/cx";
import { paths, useHashRoute, type Route } from "./lib/router";

const NAV_ITEMS = [
  { href: paths.dashboard, label: "Dashboard", match: "dashboard" },
  { href: paths.transactions, label: "Transactions", match: "transactions" },
  { href: paths.alerts, label: "Alerts", match: "alerts" },
  { href: paths.modelAdmin, label: "Model admin", match: "modelAdmin" },
];

function renderRoute(route: Route) {
  switch (route.name) {
    case "dashboard":
      return <Dashboard />;
    case "transactions":
      return <Transactions />;
    case "investigation":
      return <Investigation runId={route.runId} />;
    case "alerts":
      return <Alerts />;
    case "alertDetail":
      return <AlertDetail alertId={route.alertId} />;
    case "modelAdmin":
      return <ModelAdmin />;
    default:
      return (
        <EmptyState title="Page not found" description="The link you followed doesn't exist." />
      );
  }
}

function isActive(route: Route, match: string): boolean {
  if (match === "alerts") {
    return route.name === "alerts" || route.name === "alertDetail";
  }
  if (match === "transactions") {
    return route.name === "transactions" || route.name === "investigation";
  }
  return route.name === match;
}

export function App() {
  const route = useHashRoute();
  return (
    <div className="bg-canvas-soft text-ink min-h-screen">
      <header className="gap-md bg-canvas px-xl py-md flex items-center justify-between">
        <a href={paths.dashboard} className="font-display text-display-xs text-ink">
          FraudLens
        </a>
        <span className="text-caption text-mute">AML investigation</span>
      </header>
      <div className="max-w-container gap-xl px-xl py-xl mx-auto flex flex-col sm:flex-row">
        <nav
          aria-label="Primary"
          className="gap-xs flex shrink-0 flex-row sm:w-[200px] sm:flex-col"
        >
          {NAV_ITEMS.map((item) => {
            const active = isActive(route, item.match);
            return (
              <a
                key={item.match}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "rounded-md px-lg py-md text-body-sm font-semibold",
                  active ? "border-l-4 border-ink bg-canvas-soft text-ink" : "text-body",
                )}
              >
                {item.label}
              </a>
            );
          })}
        </nav>
        <main className="grow">{renderRoute(route)}</main>
      </div>
      <Toaster richColors position="bottom-right" />
    </div>
  );
}
