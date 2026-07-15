/**
 * Summary: The FraudLens app shell (plan §16 Phase 11, redesigned). It gates on the session
 * (`useSession`): with no session it renders the <Login/> screen, otherwise it frames the app as
 * a white app-window card over the sage canvas — a branded header (wordmark + analyst avatar +
 * sign-out), a grouped Workspace/Admin sidebar (the sole primary nav), and a sage content panel
 * that switches on the hash route (`useHashRoute`). Each page owns its own data + states; the
 * shell only routes and frames them. The Sonner `<Toaster/>` mounts once (across both the login
 * and signed-in states) so any screen can raise a toast.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - App: gate on the session, then render either <Login/> or the shell + routed page.
 *
 * Notes:
 * - The sidebar is the only nav; contextual screens (alert review, investigation) are reached by
 *   selecting a record from a list, so they need no standalone nav entry.
 * - Unknown ids deep-link straight to the relevant page (which resolves existence); an
 *   unrecognized route renders a not-found empty state.
 */
import { Suspense, lazy } from "react";
import { Toaster } from "sonner";

import { EmptyState } from "./components/feedback/EmptyState";
import { Spinner } from "./components/feedback/Spinner";
import { AlertDetail } from "./pages/AlertDetail";
import { Alerts } from "./pages/Alerts";
import { Dashboard } from "./pages/Dashboard";
import { Investigation } from "./pages/Investigation";
import { Login } from "./pages/Login";
import { ModelAdmin } from "./pages/ModelAdmin";
import { Transactions } from "./pages/Transactions";
import { cx } from "./lib/cx";

// Lazily loaded so the committed study artifact (and d3-force) load only when the research
// view is opened — the rest of the app never pulls that build-time data import.
const ResearchRoute = lazy(() => import("./pages/ResearchRoute"));
import { paths, useHashRoute, type Route } from "./lib/router";
import {
  hasPermission,
  signOut,
  useSession,
  type Session,
  type SessionPermission,
} from "./lib/session";

interface NavItem {
  label: string;
  href: string;
  permission?: SessionPermission;
  isActive: (route: Route) => boolean;
}

interface NavGroup {
  heading: string;
  items: NavItem[];
}

const SIDEBAR: NavGroup[] = [
  {
    heading: "Workspace",
    items: [
      { label: "Dashboard", href: paths.dashboard, isActive: (r) => r.name === "dashboard" },
      {
        label: "Transactions",
        href: paths.transactions,
        isActive: (r) => r.name === "transactions" || r.name === "investigation",
      },
      {
        label: "Alerts",
        href: paths.alerts,
        isActive: (r) => r.name === "alerts" || r.name === "alertDetail",
      },
    ],
  },
  {
    heading: "Research",
    items: [
      {
        label: "Graph typologies",
        href: paths.researchGraphTypologies,
        permission: "view",
        isActive: (r) => r.name === "researchGraphTypologies",
      },
    ],
  },
  {
    heading: "Admin",
    items: [
      {
        label: "Model admin",
        href: paths.modelAdmin,
        permission: "manageAdmin",
        isActive: (r) => r.name === "modelAdmin",
      },
    ],
  },
];

function roleLabel(role: Session["role"]): string {
  return {
    auditor: "Auditor",
    analyst: "Fraud Analyst",
    reviewer: "Reviewer",
    admin: "Compliance Admin",
  }[role];
}

function renderRoute(route: Route, session: Session) {
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
      if (!hasPermission(session, "manageAdmin")) {
        return <EmptyState title="Admin only" description="This page requires the admin role." />;
      }
      return <ModelAdmin />;
    case "researchGraphTypologies":
      return (
        <Suspense fallback={<Spinner label="Loading research…" />}>
          <ResearchRoute />
        </Suspense>
      );
    default:
      return (
        <EmptyState title="Page not found" description="The link you followed doesn't exist." />
      );
  }
}

export function App() {
  const session = useSession();
  const route = useHashRoute();
  if (!session) {
    return (
      <>
        <Login />
        <Toaster richColors position="bottom-right" />
      </>
    );
  }
  return (
    <div className="bg-canvas-soft text-ink min-h-screen">
      <div className="max-w-shell px-xl py-xl mx-auto flex flex-col">
        <div className="bg-canvas overflow-hidden rounded-xl">
          <header className="gap-md px-xl py-lg flex items-center justify-between">
            <a href={paths.dashboard} className="gap-sm flex items-center">
              <span aria-hidden="true" className="h-md w-md bg-primary rounded-full" />
              <span className="font-display text-display-xs text-ink">FraudLens</span>
            </a>
            <div className="gap-md flex items-center">
              <span className="text-caption text-mute">{roleLabel(session.role)}</span>
              <span className="h-2xl w-2xl bg-primary-neutral text-ink-deep text-caption flex items-center justify-center rounded-full font-semibold">
                {session.analyst.initials}
              </span>
              <button
                type="button"
                onClick={signOut}
                className="text-body-sm text-body font-semibold"
              >
                Sign out
              </button>
            </div>
          </header>

          <div className="flex flex-col md:flex-row">
            <nav
              aria-label="Workspace"
              className="gap-lg px-lg pb-lg flex shrink-0 flex-col md:w-[220px]"
            >
              {SIDEBAR.map((group) => ({
                ...group,
                items: group.items.filter(
                  (item) => !item.permission || hasPermission(session, item.permission),
                ),
              }))
                .filter((group) => group.items.length > 0)
                .map((group) => (
                  <div key={group.heading} className="gap-xxs flex flex-col">
                    <p className="text-caption text-mute px-lg py-xs font-semibold uppercase tracking-wide">
                      {group.heading}
                    </p>
                    {group.items.map((item) => {
                      const active = item.isActive(route);
                      return (
                        <a
                          key={item.label}
                          href={item.href}
                          aria-current={active ? "page" : undefined}
                          className={cx(
                            "rounded-md px-lg py-md text-body-sm font-semibold",
                            active ? "bg-canvas-soft text-ink" : "text-body",
                          )}
                        >
                          {item.label}
                        </a>
                      );
                    })}
                  </div>
                ))}
            </nav>

            <main className="bg-canvas-soft p-xl md:p-2xl grow md:rounded-tl-xl">
              {renderRoute(route, session)}
            </main>
          </div>
        </div>
      </div>
      <Toaster richColors position="bottom-right" />
    </div>
  );
}
