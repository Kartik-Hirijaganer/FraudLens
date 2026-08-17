/**
 * Summary: The accessible SVG node-link renderer for one curated laundering motif (GFP
 * study Phase 7). It is a pure presentational component: it draws the positioned nodes
 * and edges its parent computed (deterministic layout in `lib/graphLayout`), colours each
 * by owning agency (`lib/agencyStyle`), and draws directed arrowheads plus chronological
 * relative-time labels. Account nodes show their topology role and a short last-four
 * reference; the parent presents full synthetic numbers outside the drawing so the graph
 * stays legible. Edges the current tenant cannot see remain dashed, dimmed,
 * "unavailable"-labelled GHOSTS so the lost cross-tenant topology stays legible instead of
 * silently vanishing. Nodes and edges are keyboard-operable buttons with a visible focus
 * ring and full aria labels; the `<title>`/`<desc>` give the whole figure a name and summary.
 * No animation — positions are fixed, so reduced-motion users see the identical frame.
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
  role: string;
  accountNumber: string;
  accountReference: string;
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
  sequence: number;
  relativeTime: string;
  labelOffset: number;
  sourceAccountNumber: string;
  targetAccountNumber: string;
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
const NODE_RADIUS = 18;
const EDGE_START_CLEARANCE = NODE_RADIUS + 1;
const EDGE_END_CLEARANCE = NODE_RADIUS + 5;
const NODE_LABEL_EDGE_ZONE = 140;
const NODE_LABEL_TOP_ZONE = 64;
const NODE_LABEL_BOTTOM_ZONE = 64;
const NODE_LABEL_WIDTH = 180;
const NODE_LABEL_HEIGHT = 18;
const EDGE_LABEL_WIDTH = 120;
const EDGE_LABEL_HEIGHT = 18;
const UNAVAILABLE_LABEL_HEIGHT = 34;
const LABEL_CLEARANCE_STEP = 16;
const LABEL_CLEARANCE_ATTEMPTS = 4;

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface EdgeGeometry {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  labelX: number;
  labelY: number;
  labelBounds: Rect;
}

interface NodeLabelPlacement {
  textAnchor: "start" | "middle" | "end";
  labelY: number;
  bounds: Rect;
}

function isSelected(selected: GraphSelection, kind: "node" | "edge", id: string): boolean {
  return selected?.kind === kind && selected.id === id;
}

function activate(event: React.KeyboardEvent, onSelect: () => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onSelect();
  }
}

function rectanglesOverlap(left: Rect, right: Rect): boolean {
  return (
    left.x < right.x + right.width &&
    left.x + left.width > right.x &&
    left.y < right.y + right.height &&
    left.y + left.height > right.y
  );
}

function labelBounds(x: number, y: number, present: boolean): Rect {
  const height = present ? EDGE_LABEL_HEIGHT : UNAVAILABLE_LABEL_HEIGHT;
  return {
    x: x - EDGE_LABEL_WIDTH / 2,
    y: y - EDGE_LABEL_HEIGHT,
    width: EDGE_LABEL_WIDTH,
    height,
  };
}

function labelCollisionScore(bounds: Rect, blocked: Rect[], width: number, height: number): number {
  const collisionCount = blocked.filter((area) => rectanglesOverlap(bounds, area)).length;
  const outside =
    bounds.x < 0 ||
    bounds.y < 0 ||
    bounds.x + bounds.width > width ||
    bounds.y + bounds.height > height;
  return collisionCount * 2 + (outside ? 1 : 0);
}

function edgeGeometry(
  edge: EdgeView,
  blocked: Rect[],
  width: number,
  height: number,
): EdgeGeometry {
  const deltaX = edge.x2 - edge.x1;
  const deltaY = edge.y2 - edge.y1;
  const length = Math.hypot(deltaX, deltaY);
  if (length === 0) {
    const bounds = labelBounds(edge.x1, edge.y1, edge.present);
    return {
      x1: edge.x1,
      y1: edge.y1,
      x2: edge.x2,
      y2: edge.y2,
      labelX: edge.x1,
      labelY: edge.y1,
      labelBounds: bounds,
    };
  }

  const unitX = deltaX / length;
  const unitY = deltaY / length;
  const baseOffset = edge.labelOffset;
  const offsets = Array.from({ length: LABEL_CLEARANCE_ATTEMPTS }, (_, index) => {
    const offset = baseOffset + index * LABEL_CLEARANCE_STEP;
    return [offset, -offset];
  }).flat();
  const candidates = offsets.map((offset) => {
    const labelX = (edge.x1 + edge.x2) / 2 - unitY * offset;
    const labelY = (edge.y1 + edge.y2) / 2 + unitX * offset;
    const bounds = labelBounds(labelX, labelY, edge.present);
    return {
      labelX,
      labelY,
      labelBounds: bounds,
      score: labelCollisionScore(bounds, blocked, width, height),
    };
  });
  const label = candidates.reduce((best, candidate) =>
    candidate.score < best.score ? candidate : best,
  );
  return {
    x1: edge.x1 + unitX * EDGE_START_CLEARANCE,
    y1: edge.y1 + unitY * EDGE_START_CLEARANCE,
    x2: edge.x2 - unitX * EDGE_END_CLEARANCE,
    y2: edge.y2 - unitY * EDGE_END_CLEARANCE,
    labelX: label.labelX,
    labelY: label.labelY,
    labelBounds: label.labelBounds,
  };
}

function nodeTextAnchor(x: number, width: number): "start" | "middle" | "end" {
  if (x < NODE_LABEL_EDGE_ZONE) {
    return "start";
  }
  if (x > width - NODE_LABEL_EDGE_ZONE) {
    return "end";
  }
  return "middle";
}

function nodeLabelPlacement(node: NodeView, width: number, height: number): NodeLabelPlacement {
  const textAnchor = nodeTextAnchor(node.x, width);
  const placeBelow = node.y > NODE_LABEL_TOP_ZONE && node.y <= height - NODE_LABEL_BOTTOM_ZONE;
  const labelY = node.y + (placeBelow ? 38 : -28);
  const boundsX =
    textAnchor === "start"
      ? node.x
      : textAnchor === "end"
        ? node.x - NODE_LABEL_WIDTH
        : node.x - NODE_LABEL_WIDTH / 2;
  return {
    textAnchor,
    labelY,
    bounds: {
      x: boundsX,
      y: labelY - EDGE_LABEL_HEIGHT,
      width: NODE_LABEL_WIDTH,
      height: NODE_LABEL_HEIGHT,
    },
  };
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
  const nodePlacements = new Map(
    nodes.map((node) => [node.id, nodeLabelPlacement(node, width, height)] as const),
  );
  const blockedLabelAreas = [...nodePlacements.values()].map((placement) => placement.bounds);
  const edgeGeometries = new Map<string, EdgeGeometry>();
  [...edges]
    .sort((left, right) => left.sequence - right.sequence)
    .forEach((edge) => {
      const geometry = edgeGeometry(edge, blockedLabelAreas, width, height);
      edgeGeometries.set(edge.id, geometry);
      blockedLabelAreas.push(geometry.labelBounds);
    });

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

      <defs>
        {edges.map((edge, index) => {
          const style = agencyStyle(edge.ownerAgencyIndex);
          return (
            <marker
              key={edge.id}
              id={`${titleId}-arrow-${index}`}
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={10}
              markerHeight={10}
              markerUnits="userSpaceOnUse"
              orient="auto"
            >
              <path
                d="M 0 0 L 10 5 L 0 10 z"
                className={cx(style.nodeFill, edge.present ? "opacity-90" : "opacity-30")}
              />
            </marker>
          );
        })}
      </defs>

      {edges.map((edge, index) => {
        const style = agencyStyle(edge.ownerAgencyIndex);
        const chosen = isSelected(selected, "edge", edge.id);
        const geometry = edgeGeometries.get(edge.id)!;
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
              x1={geometry.x1}
              y1={geometry.y1}
              x2={geometry.x2}
              y2={geometry.y2}
              className={cx(style.edgeStroke, edge.present ? "opacity-90" : "opacity-30")}
              strokeWidth={chosen ? 4 : 2}
              strokeDasharray={edge.present ? undefined : "6 5"}
              markerEnd={`url(#${titleId}-arrow-${index})`}
              data-direction="forward"
            />
            <text
              x={geometry.labelX}
              y={geometry.labelY}
              textAnchor="middle"
              className="fill-ink stroke-canvas-soft text-caption font-semibold"
              strokeWidth={8}
              strokeLinejoin="round"
              paintOrder="stroke"
              pointerEvents="none"
            >
              #{edge.sequence} · {edge.relativeTime}
            </text>
            {!edge.present ? (
              <text
                x={geometry.labelX}
                y={geometry.labelY + 16}
                textAnchor="middle"
                className="fill-mute stroke-canvas-soft text-caption font-semibold uppercase tracking-wide"
                strokeWidth={6}
                strokeLinejoin="round"
                paintOrder="stroke"
                pointerEvents="none"
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
        const placement = nodePlacements.get(node.id)!;
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
              r={chosen ? 22 : NODE_RADIUS}
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
            <text
              x={node.x}
              y={placement.labelY}
              textAnchor={placement.textAnchor}
              className="fill-ink stroke-canvas-soft text-caption font-semibold"
              strokeWidth={7}
              strokeLinejoin="round"
              paintOrder="stroke"
              pointerEvents="none"
            >
              {node.role} · {node.accountReference}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
