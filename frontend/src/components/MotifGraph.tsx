/**
 * Summary: The accessible SVG node-link renderer for one curated laundering motif (GFP
 * study Phase 7). It is a pure presentational component: it draws the positioned nodes
 * and edges its parent computed (deterministic layout in `lib/graphLayout`), colours each
 * by owning agency (`lib/agencyStyle`), and renders edges the current tenant cannot see as
 * dashed, dimmed, "unavailable"-labelled GHOSTS so the lost cross-tenant topology stays
 * legible instead of silently vanishing. Nodes and edges are keyboard-operable buttons
 * with a visible focus ring and full aria labels; the `<title>`/`<desc>` give the whole
 * figure a name and summary. No animation — positions are fixed, so reduced-motion users
 * see the identical frame.
 *
 * Key classes:
 * - NodeView: the positioned node view model the parent passes in.
 * - EdgeView: the positioned, presence-tagged edge view model the parent passes in.
 *
 * Key functions:
 * - MotifGraph: render the motif as an accessible, ghost-aware SVG node-link diagram.
 *
 * Notes:
 * - Selection is driven by click AND focus (keyboard), so the detail panel updates without
 *   any hover — hover is never the only way to read an element (DESIGN.md accessibility).
 */
import type { CSSProperties } from "react";

import { agencyStyle } from "../lib/agencyStyle";
import { cx } from "../lib/cx";

export interface NodeView {
  id: string;
  x: number;
  y: number;
  agencyIndex: number;
  glyph: string;
  label: string;
}

export interface EdgeView {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  ownerAgencyIndex: number;
  present: boolean;
  label: string;
}

export type GraphSelection = { kind: "node" | "edge"; id: string } | null;

interface MotifGraphProps {
  titleId: string;
  descId: string;
  title: string;
  description: string;
  width: number;
  height: number;
  nodes: NodeView[];
  edges: EdgeView[];
  selected: GraphSelection;
  onSelect: (selection: GraphSelection) => void;
}

const FOCUS_RING = "outline-none focus-visible:[outline:2px_solid_theme(colors.ink.DEFAULT)]";

function isSelected(selected: GraphSelection, kind: "node" | "edge", id: string): boolean {
  return selected?.kind === kind && selected.id === id;
}

function activate(event: React.KeyboardEvent, onSelect: () => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onSelect();
  }
}

export function MotifGraph({
  titleId,
  descId,
  title,
  description,
  width,
  height,
  nodes,
  edges,
  selected,
  onSelect,
}: MotifGraphProps) {
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="group"
      aria-labelledby={titleId}
      aria-describedby={descId}
      className="h-auto w-full min-w-[var(--motif-width)] md:min-w-0"
      style={{ "--motif-width": `${width}px` } as CSSProperties}
    >
      <title id={titleId}>{title}</title>
      <desc id={descId}>{description}</desc>

      {edges.map((edge) => {
        const style = agencyStyle(edge.ownerAgencyIndex);
        const chosen = isSelected(selected, "edge", edge.id);
        const midX = (edge.x1 + edge.x2) / 2;
        const midY = (edge.y1 + edge.y2) / 2;
        return (
          <g
            key={edge.id}
            role="button"
            tabIndex={0}
            aria-label={edge.label}
            aria-pressed={chosen}
            data-present={edge.present}
            className={cx("cursor-pointer", FOCUS_RING)}
            onClick={() => onSelect({ kind: "edge", id: edge.id })}
            onFocus={() => onSelect({ kind: "edge", id: edge.id })}
            onKeyDown={(event) => activate(event, () => onSelect({ kind: "edge", id: edge.id }))}
          >
            <line
              x1={edge.x1}
              y1={edge.y1}
              x2={edge.x2}
              y2={edge.y2}
              stroke="transparent"
              strokeWidth={48}
              pointerEvents="stroke"
              data-hit-target="edge"
            />
            <line
              x1={edge.x1}
              y1={edge.y1}
              x2={edge.x2}
              y2={edge.y2}
              className={cx(style.edgeStroke, edge.present ? "opacity-90" : "opacity-30")}
              strokeWidth={chosen ? 4 : 2}
              strokeDasharray={edge.present ? undefined : "6 5"}
            />
            {!edge.present ? (
              <text
                x={midX}
                y={midY - 6}
                textAnchor="middle"
                className="fill-mute text-[10px] font-semibold uppercase tracking-wide"
              >
                unavailable
              </text>
            ) : null}
          </g>
        );
      })}

      {nodes.map((node) => {
        const style = agencyStyle(node.agencyIndex);
        const chosen = isSelected(selected, "node", node.id);
        return (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            aria-label={node.label}
            aria-pressed={chosen}
            className={cx("cursor-pointer", FOCUS_RING)}
            onClick={() => onSelect({ kind: "node", id: node.id })}
            onFocus={() => onSelect({ kind: "node", id: node.id })}
            onKeyDown={(event) => activate(event, () => onSelect({ kind: "node", id: node.id }))}
          >
            <circle cx={node.x} cy={node.y} r={24} fill="transparent" data-hit-target="node" />
            <circle
              cx={node.x}
              cy={node.y}
              r={chosen ? 22 : 18}
              className={cx(style.nodeFill, chosen && "stroke-ink")}
              strokeWidth={chosen ? 3 : 0}
            />
            <text
              x={node.x}
              y={node.y + 4}
              textAnchor="middle"
              className={cx(style.nodeText, "text-[12px] font-bold")}
            >
              {node.glyph}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
