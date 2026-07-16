/**
 * Summary: The deterministic force-directed layout adapter for the research page's motif
 * graphs (GFP study Phase 7). It is the ONLY place `d3-force` runs, and it runs it
 * deterministically: input nodes are CLONED (never mutated), each clone's initial
 * coordinate is derived from its stable id (no `Math.random` seeding), the simulation is
 * created stopped, advanced a fixed 300 ticks, and the result is fit into a constant
 * viewBox and returned as frozen positioned nodes. Same input → identical coordinates on
 * every render and machine, so snapshots and screenshots are stable and reduced-motion
 * users get the same final frame without any animation.
 *
 * Key classes:
 * - LayoutNode: one positioned node (stable id + viewBox x/y).
 * - LayoutInput: the adapter's input node + edge lists.
 * - GraphLayout: the positioned nodes plus the viewBox dimensions.
 *
 * Key functions:
 * - VIEW_WIDTH: the constant SVG viewBox width the layout is fit into.
 * - VIEW_HEIGHT: the constant SVG viewBox height the layout is fit into.
 * - layoutGraph: compute the deterministic positioned layout for one motif graph.
 *
 * Notes:
 * - D3 mutates the arrays passed to it (link source/target become node refs, x/y/vx/vy are
 *   written each tick); every array here is a private clone so callers keep immutable data.
 * - `forceCenter` keeps the drawing centred; a final fit transform rescales the settled
 *   positions into the padded viewBox so no node is ever clipped, whatever the force scale.
 */
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

export const VIEW_WIDTH = 640;
export const VIEW_HEIGHT = 420;

const TICKS = 300;
const PADDING = 48;
const LINK_DISTANCE = 96;
const CHARGE_STRENGTH = -320;
const INITIAL_RADIUS = 140;

export interface LayoutNode {
  id: string;
  x: number;
  y: number;
}

export interface LayoutInput {
  nodes: ReadonlyArray<{ id: string }>;
  edges: ReadonlyArray<{ source: string; target: string }>;
}

export interface GraphLayout {
  nodes: LayoutNode[];
  width: number;
  height: number;
}

interface SimNode extends SimulationNodeDatum {
  id: string;
}

// djb2 string hash → unsigned 32-bit, so a node's initial angle is a pure function of its id.
function hashId(id: string): number {
  let hash = 5381;
  for (let i = 0; i < id.length; i += 1) {
    hash = ((hash << 5) + hash + id.charCodeAt(i)) >>> 0;
  }
  return hash;
}

// Seed each node on a circle around the centre at a stable, id-derived angle — this replaces
// d3's random phyllotaxis seeding so the whole layout is reproducible.
function seedNode(id: string): SimNode {
  const angle = ((hashId(id) % 3600) / 3600) * 2 * Math.PI;
  return {
    id,
    x: VIEW_WIDTH / 2 + INITIAL_RADIUS * Math.cos(angle),
    y: VIEW_HEIGHT / 2 + INITIAL_RADIUS * Math.sin(angle),
    vx: 0,
    vy: 0,
  };
}

// Rescale settled coordinates into the padded viewBox so nothing is clipped, whatever the
// absolute force scale. A degenerate span (single node / colinear) centres on that axis.
function fit(nodes: SimNode[]): LayoutNode[] {
  const xs = nodes.map((node) => node.x ?? VIEW_WIDTH / 2);
  const ys = nodes.map((node) => node.y ?? VIEW_HEIGHT / 2);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  const usableW = VIEW_WIDTH - 2 * PADDING;
  const usableH = VIEW_HEIGHT - 2 * PADDING;
  const scale = Math.min(
    spanX > 0 ? usableW / spanX : Infinity,
    spanY > 0 ? usableH / spanY : Infinity,
  );
  const factor = Number.isFinite(scale) ? scale : 1;

  const place = (value: number, min: number, span: number, usable: number, offset: number) =>
    span > 0 ? offset + (value - min) * factor + (usable - span * factor) / 2 : offset + usable / 2;

  return nodes.map((node) =>
    Object.freeze({
      id: node.id,
      x: Math.round(place(node.x ?? minX, minX, spanX, usableW, PADDING)),
      y: Math.round(place(node.y ?? minY, minY, spanY, usableH, PADDING)),
    }),
  );
}

export function layoutGraph(input: LayoutInput): GraphLayout {
  // Private clones — d3 mutates these; the caller's arrays are never touched.
  const simNodes: SimNode[] = input.nodes.map((node) => seedNode(node.id));
  const simLinks: SimulationLinkDatum<SimNode>[] = input.edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
  }));

  const simulation = forceSimulation<SimNode>(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((node) => node.id)
        .distance(LINK_DISTANCE),
    )
    .force("charge", forceManyBody().strength(CHARGE_STRENGTH))
    .force("center", forceCenter(VIEW_WIDTH / 2, VIEW_HEIGHT / 2))
    .stop();

  for (let tick = 0; tick < TICKS; tick += 1) {
    simulation.tick();
  }

  return {
    nodes: fit(simNodes),
    width: VIEW_WIDTH,
    height: VIEW_HEIGHT,
  };
}
