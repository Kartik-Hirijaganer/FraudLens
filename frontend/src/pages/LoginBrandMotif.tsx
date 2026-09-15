/**
 * Summary: Decorative reduced-motion-aware brand motif for the login panel.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - BrandMotif: render tokenized grid, connector, and pulse-node decoration.
 *
 * Notes:
 * - The motif is aria-hidden and never carries authentication meaning.
 */
import { cx } from "../lib/cx";

// The two right-angle connector paths drawn across the grid.
const MOTIF_LINES = [
  "0,620 112,620 112,340 280,340 280,508 448,508 448,228 640,228",
  "0,760 168,760 168,600 336,600 336,676 560,676 560,452 640,452",
] as const;

// Pulse-nodes positioned over the grid; each breathes on its own duration/phase.
const MOTIF_NODES = [
  {
    pos: "right-[80px] top-1/4",
    size: "size-[10px]",
    tone: "bg-auth-cyan shadow-node-cyan",
    dur: "2.4s",
    delay: "0s",
  },
  {
    pos: "right-[180px] top-[56%]",
    size: "size-[8px]",
    tone: "bg-auth-amber shadow-node-amber",
    dur: "3s",
    delay: "0.5s",
  },
  {
    pos: "left-[140px] top-[33%]",
    size: "size-[7px]",
    tone: "bg-canvas/70",
    dur: "3.1s",
    delay: "0.9s",
  },
  {
    pos: "left-[220px] top-[70%]",
    size: "size-[8px]",
    tone: "bg-auth-green shadow-node-green",
    dur: "2.7s",
    delay: "1.3s",
  },
  {
    pos: "right-[300px] top-[80%]",
    size: "size-[6px]",
    tone: "bg-canvas/50",
    dur: "3.4s",
    delay: "0.3s",
  },
] as const;

export function BrandMotif() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="bg-auth-glow absolute inset-0" />
      <div className="bg-auth-grid-coarse motion-safe:animate-grid-pan absolute inset-0 bg-[length:56px_56px] opacity-90" />
      <div className="bg-auth-grid-fine motion-safe:animate-grid-pan absolute inset-0 bg-[length:14px_14px] opacity-50" />
      <svg
        viewBox="0 0 640 900"
        preserveAspectRatio="none"
        className="absolute inset-0 size-full text-white opacity-90"
      >
        {MOTIF_LINES.map((points, i) => (
          <polyline
            key={points}
            points={points}
            fill="none"
            stroke={i === 0 ? undefined : "currentColor"}
            className={cx(i === 0 && "stroke-auth-cyan", "motion-safe:animate-draw")}
            strokeWidth="1.5"
            strokeOpacity={i === 0 ? "0.42" : "0.22"}
            strokeDasharray="1700"
            style={{
              animationDuration: i === 0 ? "3s" : "3.2s",
              animationDelay: i === 0 ? "0.4s" : "0.7s",
            }}
          />
        ))}
      </svg>
      <div className="bg-auth-sheen motion-safe:animate-sheen absolute inset-y-0 w-[180px] translate-x-[-140%]" />
      {MOTIF_NODES.map((node) => (
        <span
          key={node.pos}
          className={cx(
            "motion-safe:animate-node-pulse absolute rounded-full",
            node.pos,
            node.size,
            node.tone,
          )}
          style={{ animationDuration: node.dur, animationDelay: node.delay }}
        />
      ))}
    </div>
  );
}
