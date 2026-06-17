/**
 * Summary: A horizontal bar chart of the top SHAP drivers behind a score (plan §16
 * Phase 11 "SHAP" chart). Each feature's bar length is proportional to the magnitude of
 * its signed contribution; bars that push the score toward fraud use the negative tone,
 * those that pull it down use the positive tone — so the explanation reads at a glance.
 * Renders an EmptyState until the explainer step has run.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ShapBarChart: render the SHAP driver bars (or an empty state).
 *
 * Notes:
 * - Bar widths are data-driven percentages set via inline style (the only place a runtime
 * value can't be a design-token class); colours/spacing stay tokens.
 */
import { cx } from "../lib/cx";
import type { ShapFeature } from "../lib/investigation";
import { EmptyState } from "./feedback/EmptyState";

interface ShapBarChartProps {
  features: ShapFeature[];
}

export function ShapBarChart({ features }: ShapBarChartProps) {
  if (features.length === 0) {
    return (
      <EmptyState
        title="No explanation yet"
        description="SHAP drivers appear once the scoring step completes."
      />
    );
  }
  const maxAbs = Math.max(...features.map((feature) => Math.abs(feature.shapValue)), 1e-9);
  return (
    <ul className="gap-sm flex flex-col">
      {features.map((feature) => {
        const widthPct = Math.round((Math.abs(feature.shapValue) / maxAbs) * 100);
        const increasesRisk = feature.shapValue >= 0;
        return (
          <li key={feature.feature} className="gap-xxs flex flex-col">
            <div className="gap-md flex items-baseline justify-between">
              <span className="text-body-sm text-ink font-semibold">{feature.feature}</span>
              <span className="text-caption text-mute">
                {increasesRisk ? "+" : ""}
                {feature.shapValue.toFixed(3)}
              </span>
            </div>
            <div className="h-md rounded-pill bg-canvas-soft w-full overflow-hidden">
              <div
                className={cx("h-full rounded-pill", increasesRisk ? "bg-negative" : "bg-positive")}
                style={{ width: `${widthPct}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
