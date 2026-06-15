/**
 * Summary: A tiny dependency-free hash router for the single-page analyst/admin app
 * (plan §16 Phase 11). The SPA is served as a static bundle behind the gateway, so
 * `location.hash` routing needs no server rewrite rules and no router dependency.
 * `parseHash` turns a raw hash into a typed `Route` (a discriminated union the app
 * switches on), `useHashRoute` re-renders on `hashchange`, `navigate` changes the
 * route, and `paths` builds the canonical hrefs in one place (rule 5: no duplication).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - parseHash: parse a location hash into a typed Route (notFound for anything else).
 * - useHashRoute: subscribe to hashchange and return the current Route.
 * - navigate: change the current route by setting the location hash.
 * - paths: canonical href builders for every route.
 *
 * Notes:
 * - Unknown ids in `#/alerts/:id` / `#/investigations/:runId` are still routed (the page
 *   resolves existence and renders a not-found / error state), so deep links never 404.
 */
import { useEffect, useState } from "react";

export type Route =
  | { name: "dashboard" }
  | { name: "transactions" }
  | { name: "alerts" }
  | { name: "alertDetail"; alertId: string }
  | { name: "investigation"; runId: string }
  | { name: "modelAdmin" }
  | { name: "notFound" };

export function parseHash(hash: string): Route {
  const segments = hash.replace(/^#/, "").split("/").filter(Boolean);
  if (segments.length === 0) {
    return { name: "dashboard" };
  }
  const [head, second] = segments;
  if (head === "transactions" && segments.length === 1) {
    return { name: "transactions" };
  }
  if (head === "alerts") {
    if (segments.length === 1) {
      return { name: "alerts" };
    }
    if (segments.length === 2) {
      return { name: "alertDetail", alertId: second };
    }
  }
  if (head === "investigations" && segments.length === 2) {
    return { name: "investigation", runId: second };
  }
  if (head === "model-admin" && segments.length === 1) {
    return { name: "modelAdmin" };
  }
  return { name: "notFound" };
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = (): void => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(to: string): void {
  window.location.hash = to;
}

export const paths = {
  dashboard: "#/",
  transactions: "#/transactions",
  alerts: "#/alerts",
  modelAdmin: "#/model-admin",
  alertDetail: (alertId: string): string => `#/alerts/${alertId}`,
  investigation: (runId: string): string => `#/investigations/${runId}`,
};
