import { describe, expect, it } from "vitest";

import { VIEW_HEIGHT, VIEW_WIDTH, layoutGraph } from "./graphLayout";

const TRIANGLE = {
  nodes: [{ id: "node-01" }, { id: "node-02" }, { id: "node-03" }],
  edges: [
    { source: "node-01", target: "node-02" },
    { source: "node-02", target: "node-03" },
    { source: "node-03", target: "node-01" },
  ],
};

describe("layoutGraph", () => {
  it("positions every node inside the viewBox", () => {
    const layout = layoutGraph(TRIANGLE);
    expect(layout.width).toBe(VIEW_WIDTH);
    expect(layout.height).toBe(VIEW_HEIGHT);
    expect(layout.nodes).toHaveLength(3);
    for (const node of layout.nodes) {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.x).toBeLessThanOrEqual(VIEW_WIDTH);
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.y).toBeLessThanOrEqual(VIEW_HEIGHT);
    }
  });

  it("is deterministic — same input yields identical coordinates", () => {
    const first = layoutGraph(TRIANGLE);
    const second = layoutGraph(TRIANGLE);
    expect(second.nodes).toEqual(first.nodes);
  });

  it("returns frozen node objects and never mutates the input", () => {
    const input = {
      nodes: [{ id: "node-01" }, { id: "node-02" }],
      edges: [{ source: "node-01", target: "node-02" }],
    };
    const snapshot = JSON.stringify(input);
    const layout = layoutGraph(input);
    expect(Object.isFrozen(layout.nodes[0])).toBe(true);
    // The caller's arrays are untouched even though d3 mutates its own private clones.
    expect(JSON.stringify(input)).toBe(snapshot);
  });

  it("handles the degenerate single-node graph without NaN", () => {
    const layout = layoutGraph({ nodes: [{ id: "only" }], edges: [] });
    expect(layout.nodes).toHaveLength(1);
    expect(Number.isFinite(layout.nodes[0].x)).toBe(true);
    expect(Number.isFinite(layout.nodes[0].y)).toBe(true);
  });

  it("gives distinct nodes distinct positions", () => {
    const layout = layoutGraph(TRIANGLE);
    const keys = new Set(layout.nodes.map((node) => `${node.x},${node.y}`));
    expect(keys.size).toBe(layout.nodes.length);
  });
});
