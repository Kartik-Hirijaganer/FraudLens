import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Research, ADR_017_HREF } from "./Research";
import type { GfpStudyData, StudyHighlightMetrics } from "../lib/gfpStudy";

function metrics(overrides: Partial<StudyHighlightMetrics> = {}): StudyHighlightMetrics {
  return {
    datasetSource: "ibm-aml",
    armAPrAuc: 0.2,
    armCPrAuc: 0.25,
    armCPrAucNormalized: 25,
    armAToCLift: 0.05,
    armAToCCiLower: 0.01,
    armAToCCiUpper: 0.07,
    isolationDeltaC: 0.02,
    ...overrides,
  };
}

function studyData(overrides: Partial<StudyHighlightMetrics> = {}): GfpStudyData {
  return {
    reportSha256: "a".repeat(64),
    metrics: metrics(overrides),
    agencyNames: ["Demo Financial Agency", "AML Demo Agency Two", "AML Demo Agency Three"],
    motifs: [
      {
        motifId: "scatter_gather-1",
        typology: "scatter_gather",
        servable: true,
        nodes: [
          { nodeId: "node-01", agencyIndex: 0 },
          { nodeId: "node-02", agencyIndex: 0 },
          { nodeId: "node-03", agencyIndex: 0 },
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
            sourceNodeId: "node-01",
            targetNodeId: "node-03",
            timeOffsetS: 60,
            amountBand: "1k-10k",
            ownerAgencyIndex: 0,
          },
        ],
      },
      {
        motifId: "intra_tenant_cycle-1",
        typology: "intra_tenant_cycle",
        servable: true,
        nodes: [
          { nodeId: "node-01", agencyIndex: 0 },
          { nodeId: "node-02", agencyIndex: 0 },
        ],
        edges: [
          {
            edgeId: "edge-01",
            sourceNodeId: "node-01",
            targetNodeId: "node-02",
            timeOffsetS: 0,
            amountBand: "100-1k",
            ownerAgencyIndex: 0,
          },
          {
            edgeId: "edge-02",
            sourceNodeId: "node-02",
            targetNodeId: "node-01",
            timeOffsetS: 60,
            amountBand: "100-1k",
            ownerAgencyIndex: 0,
          },
        ],
      },
      {
        motifId: "cross_tenant_cycle-1",
        typology: "cross_tenant_cycle",
        servable: false,
        nodes: [
          { nodeId: "node-01", agencyIndex: 0 },
          { nodeId: "node-02", agencyIndex: 1 },
          { nodeId: "node-03", agencyIndex: 2 },
        ],
        edges: [
          {
            edgeId: "edge-01",
            sourceNodeId: "node-01",
            targetNodeId: "node-02",
            timeOffsetS: 0,
            amountBand: "100-1k",
            ownerAgencyIndex: 0,
          },
          {
            edgeId: "edge-02",
            sourceNodeId: "node-02",
            targetNodeId: "node-03",
            timeOffsetS: 60,
            amountBand: "100-1k",
            ownerAgencyIndex: 1,
          },
          {
            edgeId: "edge-03",
            sourceNodeId: "node-03",
            targetNodeId: "node-01",
            timeOffsetS: 120,
            amountBand: "100-1k",
            ownerAgencyIndex: 2,
          },
        ],
      },
    ],
  };
}

describe("Research", () => {
  it("renders the signed hero metrics and the ADR-017 link", () => {
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    expect(screen.getByText("0.2500")).toBeInTheDocument(); // Arm C PR-AUC
    expect(screen.getByText("25.0×")).toBeInTheDocument(); // normalized lift
    expect(screen.getByText("+0.0500")).toBeInTheDocument(); // A->C lift
    expect(screen.getByText("+0.0200")).toBeInTheDocument(); // signed isolation delta
    const adr = screen.getByRole("link", { name: /ADR-017/ });
    expect(adr).toHaveAttribute("href", ADR_017_HREF);
  });

  it("names the isolation stat a cost only for a positive delta", () => {
    const positive = render(<Research data={studyData()} viewerAgencyIndex={0} />);
    expect(screen.getByText(/Cost of isolation/)).toBeInTheDocument();
    positive.unmount();

    render(<Research data={studyData({ isolationDeltaC: -0.01 })} viewerAgencyIndex={0} />);
    expect(screen.queryByText(/Cost of isolation/)).not.toBeInTheDocument();
    expect(screen.getByText(/Isolation delta/)).toBeInTheDocument();
  });

  it("shows the public-synthetic-data banner", () => {
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    expect(
      screen.getByText(/Public synthetic offline study — not live tenant data/),
    ).toBeInTheDocument();
  });

  it("switches the motif via the typology control", async () => {
    const user = userEvent.setup();
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    expect(screen.getByRole("group", { name: /Scatter/ })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Cross-tenant cycle/ }));
    expect(screen.getByRole("group", { name: /Cross-tenant cycle/ })).toBeInTheDocument();
  });

  it("defaults the tenant scope to the viewer agency and ghosts other agencies' edges", async () => {
    const user = userEvent.setup();
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    await user.click(screen.getByRole("radio", { name: /Cross-tenant cycle/ }));

    // Global: every edge present.
    expect(screen.queryByText("unavailable")).not.toBeInTheDocument();
    const owned = screen.getByRole("button", { name: /Edge edge-01/ });
    expect(owned).toHaveAttribute("data-present", "true");

    // Switch to the viewer's agency (index 0): edges owned by agencies 1 & 2 ghost out.
    await user.click(screen.getByRole("radio", { name: "Demo Financial Agency" }));
    expect(screen.getAllByText("unavailable").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Edge edge-02.*unavailable/ })).toHaveAttribute(
      "data-present",
      "false",
    );
    // The current tenant's own edge is still solid.
    expect(screen.getByRole("button", { name: /Edge edge-01/ })).toHaveAttribute(
      "data-present",
      "true",
    );
  });

  it("resolves each agency persona into its own scope control", () => {
    const one = render(<Research data={studyData()} viewerAgencyIndex={0} />);
    expect(screen.getByRole("radio", { name: "Demo Financial Agency" })).toBeInTheDocument();
    one.unmount();

    const two = render(<Research data={studyData()} viewerAgencyIndex={1} />);
    expect(screen.getByRole("radio", { name: "AML Demo Agency Two" })).toBeInTheDocument();
    two.unmount();

    // A non-demo / absent agency falls back to the first agency.
    render(<Research data={studyData()} viewerAgencyIndex={null} />);
    expect(screen.getByRole("radio", { name: "Demo Financial Agency" })).toBeInTheDocument();
  });

  it("shows the agency legend with letters and names (colour is not the only channel)", () => {
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    const legend = screen.getByRole("list");
    expect(within(legend).getByText("A")).toBeInTheDocument();
    expect(within(legend).getByText("Demo Financial Agency")).toBeInTheDocument();
  });

  it("updates the non-hover detail panel when a node or edge is selected", async () => {
    const user = userEvent.setup();
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    await user.click(screen.getByRole("button", { name: /Node node-01/ }));
    const detail = screen.getByText(/Node node-01/, { selector: "p" });
    expect(detail).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Edge edge-01/ }));
    expect(
      screen.getByText(/Edge edge-01 from node-01 to node-02/, { selector: "p" }),
    ).toBeInTheDocument();
  });

  it("activates a node from the keyboard (Enter)", async () => {
    const user = userEvent.setup();
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    const node = screen.getByRole("button", { name: /Node node-02/ });
    node.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByText(/Node node-02/, { selector: "p" })).toBeInTheDocument();
  });

  it("selects an edge on focus and on the Space key", async () => {
    const user = userEvent.setup();
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    // Focus alone (keyboard tabbing) selects — no hover needed.
    act(() => screen.getByRole("button", { name: /Edge edge-02/ }).focus());
    expect(screen.getByText(/Edge edge-02/, { selector: "p" })).toBeInTheDocument();
    // Space activates the focused element too.
    const node = screen.getByRole("button", { name: /Node node-03/ });
    act(() => node.focus());
    await user.keyboard(" ");
    expect(screen.getByText(/Node node-03/, { selector: "p" })).toBeInTheDocument();
  });

  it("lists every node and edge in the text alternative", () => {
    render(<Research data={studyData()} viewerAgencyIndex={0} />);
    const accounts = screen.getByRole("table", { name: /Accounts/ });
    expect(within(accounts).getByText("node-01")).toBeInTheDocument();
    const transfers = screen.getByRole("table", { name: /Transfers/ });
    expect(within(transfers).getByText("edge-01")).toBeInTheDocument();
  });
});
