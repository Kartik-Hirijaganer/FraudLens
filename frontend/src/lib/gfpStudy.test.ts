import { describe, expect, it } from "vitest";

import { TYPOLOGIES, parseStudyData } from "./gfpStudy";

// A minimal but fully-valid committed payload; individual tests clone + corrupt one field to
// prove the parser rejects malformed / drifted data rather than rendering it.
function validData(): Record<string, unknown> {
  return {
    reportSha256: "a".repeat(64),
    metrics: {
      datasetSource: "ibm-aml",
      armAPrAuc: 0.2,
      armCPrAuc: 0.25,
      armCPrAucNormalized: 25,
      armAToCLift: 0.05,
      armAToCCiLower: 0.01,
      armAToCCiUpper: 0.07,
      isolationDeltaC: 0.02,
    },
    agencyNames: ["Agency One", "Agency Two", "Agency Three"],
    motifs: [
      {
        motifId: "cross_tenant_cycle-abc",
        typology: "cross_tenant_cycle",
        servable: false,
        nodes: [
          { nodeId: "node-01", agencyIndex: 0 },
          { nodeId: "node-02", agencyIndex: 1 },
        ],
        edges: [
          {
            edgeId: "edge-01",
            sourceNodeId: "node-01",
            targetNodeId: "node-02",
            timeOffsetS: 0,
            amountBand: "1k-10k",
            ownerAgencyIndex: 0,
          },
          {
            edgeId: "edge-02",
            sourceNodeId: "node-02",
            targetNodeId: "node-01",
            timeOffsetS: 60,
            amountBand: "1k-10k",
            ownerAgencyIndex: 1,
          },
        ],
      },
    ],
  };
}

describe("parseStudyData", () => {
  it("parses a well-formed payload and keeps the typology set", () => {
    const data = parseStudyData(validData());
    expect(data.motifs).toHaveLength(1);
    expect(data.motifs[0].typology).toBe("cross_tenant_cycle");
    expect(data.metrics.isolationDeltaC).toBe(0.02);
    expect(TYPOLOGIES).toContain(data.motifs[0].typology);
  });

  it.each([
    ["a non-object root", () => 42, /must be an object/],
    ["a short report hash", () => ({ ...validData(), reportSha256: "abc" }), /64-character hex/],
    ["empty agency names", () => ({ ...validData(), agencyNames: [] }), /at least one agency/],
    ["no motifs", () => ({ ...validData(), motifs: [] }), /at least one curated motif/],
  ])("rejects %s", (_label, build, matcher) => {
    expect(() => parseStudyData(build())).toThrow(matcher);
  });

  function withMotif(mutate: (motif: Record<string, unknown>) => void): unknown {
    const data = validData();
    mutate((data.motifs as Record<string, unknown>[])[0]);
    return data;
  }

  it("rejects an unknown typology", () => {
    expect(() => parseStudyData(withMotif((m) => (m.typology = "smurfing")))).toThrow(
      /not a known typology/,
    );
  });

  it("rejects a motif with fewer than two nodes or no edges", () => {
    expect(() => parseStudyData(withMotif((m) => (m.nodes = [(m.nodes as unknown[])[0]])))).toThrow(
      /at least two nodes/,
    );
    expect(() => parseStudyData(withMotif((m) => (m.edges = [])))).toThrow(/at least one edge/);
  });

  it("rejects an edge that references an undeclared node (drift)", () => {
    expect(() =>
      parseStudyData(
        withMotif((m) => {
          (m.edges as Record<string, unknown>[])[0].targetNodeId = "node-99";
        }),
      ),
    ).toThrow(/undeclared node/);
  });

  it("rejects an agency index beyond the declared agency list (drift)", () => {
    expect(() =>
      parseStudyData(
        withMotif((m) => {
          (m.nodes as Record<string, unknown>[])[0].agencyIndex = 9;
        }),
      ),
    ).toThrow(/data drifted from the agency list/);
  });

  it("rejects a multi-owner motif marked servable", () => {
    expect(() => parseStudyData(withMotif((m) => (m.servable = true)))).toThrow(
      /cannot be servable/,
    );
  });

  it("rejects a negative relative time offset", () => {
    expect(() =>
      parseStudyData(
        withMotif((m) => {
          (m.edges as Record<string, unknown>[])[0].timeOffsetS = -1;
        }),
      ),
    ).toThrow(/must be >= 0/);
  });

  it("rejects a non-boolean servable flag", () => {
    expect(() => parseStudyData(withMotif((m) => (m.servable = "yes")))).toThrow(
      /servable must be a boolean/,
    );
  });

  it("rejects a non-finite metric and an inverted interval", () => {
    const nan = validData();
    (nan.metrics as Record<string, unknown>).armCPrAuc = Number.NaN;
    expect(() => parseStudyData(nan)).toThrow(/must be a finite number/);

    const inverted = validData();
    (inverted.metrics as Record<string, unknown>).armAToCCiLower = 0.9;
    expect(() => parseStudyData(inverted)).toThrow(/interval lower bound exceeds/);
  });

  it("rejects a missing metrics block", () => {
    const data = validData();
    delete data.metrics;
    expect(() => parseStudyData(data)).toThrow(/metrics must be an object/);
  });
});
