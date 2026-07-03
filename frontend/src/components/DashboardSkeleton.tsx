/**
 * Summary: The loading placeholder for the analyst dashboard. It mirrors the real layout
 * — greeting line, four KPI cards, and a queue card of rows — with pulsing `Skeleton`
 * blocks so the page keeps its shape while metrics + alerts load, avoiding layout shift.
 * The only motion is `Skeleton`'s `motion-safe` pulse (suppressed under reduced-motion).
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - DashboardSkeleton: render the dashboard's loading placeholder.
 *
 * Notes:
 * - Decorative only (`aria-hidden`): screen readers announce the real content once loaded,
 *   never the placeholder.
 */
import { Skeleton } from "./feedback/Skeleton";

const KPI_KEYS = ["open", "review", "sars", "model"] as const;
const ROW_KEYS = ["r1", "r2", "r3", "r4"] as const;

export function DashboardSkeleton() {
  return (
    <section aria-hidden="true" className="gap-2xl flex flex-col">
      <div className="gap-sm flex flex-col">
        <Skeleton className="h-2xl w-2/3 max-w-md" />
        <Skeleton className="h-lg w-1/2 max-w-sm" />
      </div>

      <div className="gap-lg grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        {KPI_KEYS.map((key) => (
          <div key={key} className="gap-md bg-canvas p-xl flex flex-col rounded-xl">
            <Skeleton className="h-md w-1/2" />
            <Skeleton className="h-2xl w-2/3" />
            <Skeleton className="h-md w-full" />
          </div>
        ))}
      </div>

      <div className="gap-lg bg-canvas p-xl flex flex-col rounded-xl">
        <Skeleton className="h-xl w-1/4" />
        {ROW_KEYS.map((key) => (
          <Skeleton key={key} className="h-3xl w-full" />
        ))}
      </div>
    </section>
  );
}
