/**
 * Summary: The animated circular fraud-risk gauge (plan §16 Phase 11 "animated gauges").
 * It renders the blended risk as a ring that fills from 0 to the value on mount, coloured
 * by the risk band via the semantic palette (`riskTone`) — never the brand green. The fill
 * animates with a CSS stroke transition, which is skipped when the user prefers reduced
 * motion (the gauge then renders its final state immediately). Exposes `role="meter"` with
 * an aria value + text so the score is announced accessibly.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - FraudGauge: render the accessible animated risk gauge.
 *
 * Notes:
 * - `value` is clamped to [0,1]; a non-finite value renders as 0 so the gauge never breaks.
 */
import { useEffect, useState } from "react";

import { cx } from "../lib/cx";
import { formatPercent, humanize } from "../lib/format";
import { usePrefersReducedMotion } from "../lib/motion";
import { riskTone, type StatusTone } from "../lib/risk";
import { Badge } from "./ui/Badge";

const RADIUS = 52;
const STROKE = 12;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const STROKE_CLASSES: Record<StatusTone, string> = {
  positive: "stroke-positive",
  warning: "stroke-warning",
  negative: "stroke-negative",
  neutral: "stroke-mute",
};

interface FraudGaugeProps {
  value: number;
  band: string;
}

export function FraudGauge({ value, band }: FraudGaugeProps) {
  const reduced = usePrefersReducedMotion();
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const [filled, setFilled] = useState(reduced);
  useEffect(() => {
    setFilled(true);
  }, []);
  const fraction = filled ? clamped : 0;
  const tone = riskTone(band);
  const percent = Math.round(clamped * 100);
  const offset = CIRCUMFERENCE * (1 - fraction);
  return (
    <div className="gap-sm flex flex-col items-center">
      <svg
        viewBox="0 0 128 128"
        className="size-[160px]"
        role="meter"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${humanize(band)} risk, ${formatPercent(clamped)}`}
      >
        <circle
          cx={64}
          cy={64}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          className="stroke-canvas-soft"
        />
        <circle
          cx={64}
          cy={64}
          r={RADIUS}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          transform="rotate(-90 64 64)"
          className={cx(
            STROKE_CLASSES[tone],
            !reduced && "transition-[stroke-dashoffset] duration-700",
          )}
        />
        <text x={64} y={62} textAnchor="middle" className="fill-ink text-display-xs font-semibold">
          {percent}%
        </text>
        <text x={64} y={84} textAnchor="middle" className="fill-body text-caption">
          fraud risk
        </text>
      </svg>
      <Badge tone={tone}>{humanize(band)}</Badge>
    </div>
  );
}
