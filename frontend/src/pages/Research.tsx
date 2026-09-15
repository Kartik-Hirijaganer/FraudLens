/**
 * Summary: The authenticated research page at `#/research/graph-typologies` (GFP study
 * Phase 7). It makes the tenant-isolation trade-off VISIBLE: a hero of the study's signed
 * headline metrics, a prominent "public synthetic offline study — not live tenant data"
 * banner with the ADR-017 link, three laundering-motif tabs, and a Global / current-agency
 * scope control. In the agency view, edges the current tenant owns stay solid and edges
 * owned by other agencies render as dashed "unavailable" ghosts — so the cross-tenant
 * cycle's edges visibly disappear when you switch to a single agency. Directed arrowheads,
 * transaction order, relative study time, topology roles, and deterministic full synthetic
 * account aliases make each pattern readable without implying that the artifact contains real
 * bank data. Everything is driven by the one committed, redacted study artifact
 * (`lib/gfpStudy`); there is no backend call and no cross-tenant query. Colour uses only wise
 * tokens (ink / cyan / orange, never the reserved primary green) plus a letter channel + legend
 * so colour is never load-bearing.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ADR_017_HREF: bundle the canonical ADR text into a UTF-8 browser-readable URL.
 * - Research: render the finding, metric hero, banner, motif tabs, scope control, graph, panels.
 *
 * Notes:
 * - The isolation-delta copy says "isolation delta" and only "cost of isolation" when the
 * signed delta is positive; the lift resume reads positive only when the interval supports it.
 * - `headlineFinding` leads the page because the four tiles state PR-AUC and a normalized multiple
 * but not the CONCLUSION; it is derived from the artifact and its wording follows the measured
 * signs, so a zero or negative isolation delta reads as the valid result ADR-017 says it is.
 * - The partition anchor names which study partition the runtime demo agency mirrors WITHOUT
 * calling it a tenant: partitions are an offline analysis concept and exactly one runtime tenant
 * exists (ADR-017, ADR-018). Model admin links here so the served contract's single-hop limit is
 * an answered question rather than an unexplained absence.
 * - The current-agency option is bound to the viewer's VERIFIED agency (never client-selected);
 * a text-alternative table lists every node and edge for non-visual and keyboard users.
 */
import { useMemo, useState } from "react";

import adr017Text from "../../../docs/architecture/adr/ADR-017-graph-feature-serving-boundary.md?raw";
import { type EdgeView, type GraphSelection, type NodeView } from "../components/MotifGraph";
import { StatTile } from "../components/ui/StatTile";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { PageHeader } from "../components/ui/PageHeader";
import { agencyStyle } from "../lib/agencyStyle";
import { layoutGraph } from "../lib/graphLayout";
import { TYPOLOGIES, type GfpStudyData, type Typology } from "../lib/gfpStudy";
import { ResearchGraphCanvas } from "./ResearchGraphCanvas";
import {
  PARALLEL_EDGE_LABEL_OFFSET,
  SINGLE_EDGE_LABEL_OFFSET,
  TYPOLOGY_LABELS,
  type Scope,
  accountReference,
  accountRole,
  clampAgency,
  edgePair,
  formatRelativeTime,
  headlineFinding,
  signed,
  syntheticAccountNumber,
} from "./researchGraphLabels";

function createAdrHref(): string {
  if (typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(new Blob([adr017Text], { type: "text/plain;charset=utf-8" }));
  }
  return `data:text/plain;charset=utf-8,${encodeURIComponent(adr017Text)}`;
}

export const ADR_017_HREF = createAdrHref();

interface ResearchProps {
  data: GfpStudyData;
  // The viewer's verified agency index within the study, or null when it is not a demo agency.
  viewerAgencyIndex: number | null;
}

export function Research({ data, viewerAgencyIndex }: ResearchProps) {
  const available = useMemo(
    () => TYPOLOGIES.filter((typology) => data.motifs.some((motif) => motif.typology === typology)),
    [data.motifs],
  );
  const [activeTypology, setActiveTypology] = useState<Typology>(available[0]);
  const [scope, setScope] = useState<Scope>("tenant");
  const [selected, setSelected] = useState<GraphSelection>(null);

  const tenantIndex = clampAgency(viewerAgencyIndex, data.agencyNames.length);
  const tenantName = data.agencyNames[tenantIndex];
  const motif =
    data.motifs.find((candidate) => candidate.typology === activeTypology) ?? data.motifs[0];

  const layout = useMemo(
    () =>
      layoutGraph({
        nodes: motif.nodes.map((node) => ({ id: node.nodeId })),
        edges: motif.edges.map((edge) => ({
          source: edge.sourceNodeId,
          target: edge.targetNodeId,
        })),
      }),
    [motif],
  );
  const position = useMemo(
    () => new Map(layout.nodes.map((node) => [node.id, node])),
    [layout.nodes],
  );

  const agencyName = (index: number): string => data.agencyNames[index] ?? `Agency ${index}`;

  const nodeViews: NodeView[] = motif.nodes.map((node, nodeIndex) => {
    const coords = position.get(node.nodeId) ?? { x: layout.width / 2, y: layout.height / 2 };
    const accountNumber = syntheticAccountNumber(node.agencyIndex, nodeIndex);
    const role = accountRole(node.nodeId, motif.edges);
    return {
      id: node.nodeId,
      x: coords.x,
      y: coords.y,
      agencyIndex: node.agencyIndex,
      glyph: agencyStyle(node.agencyIndex).letter,
      role,
      accountNumber,
      accountReference: accountReference(accountNumber),
      label:
        `Node ${node.nodeId}, ${role}, synthetic account ${accountNumber}, ` +
        `${agencyName(node.agencyIndex)} (agency ${agencyStyle(node.agencyIndex).letter})`,
    };
  });

  const accountNumberByNode = new Map(
    nodeViews.map((node) => [node.id, node.accountNumber] as const),
  );
  const edgeSequence = new Map(
    [...motif.edges]
      .sort(
        (left, right) =>
          left.timeOffsetS - right.timeOffsetS || left.edgeId.localeCompare(right.edgeId),
      )
      .map((edge, index) => [edge.edgeId, index + 1] as const),
  );
  const pairCounts = motif.edges.reduce<Map<string, number>>((counts, edge) => {
    const pair = edgePair(edge.sourceNodeId, edge.targetNodeId);
    counts.set(pair, (counts.get(pair) ?? 0) + 1);
    return counts;
  }, new Map());

  const edgeViews: EdgeView[] = motif.edges.map((edge) => {
    const source = position.get(edge.sourceNodeId);
    const target = position.get(edge.targetNodeId);
    const present = scope === "global" || edge.ownerAgencyIndex === tenantIndex;
    const sequence = edgeSequence.get(edge.edgeId) ?? 1;
    const relativeTime = formatRelativeTime(edge.timeOffsetS);
    const sourceAccountNumber = accountNumberByNode.get(edge.sourceNodeId) ?? "unknown";
    const targetAccountNumber = accountNumberByNode.get(edge.targetNodeId) ?? "unknown";
    const hasParallelEdge =
      (pairCounts.get(edgePair(edge.sourceNodeId, edge.targetNodeId)) ?? 0) > 1;
    return {
      id: edge.edgeId,
      x1: source?.x ?? 0,
      y1: source?.y ?? 0,
      x2: target?.x ?? 0,
      y2: target?.y ?? 0,
      ownerAgencyIndex: edge.ownerAgencyIndex,
      present,
      sequence,
      relativeTime,
      labelOffset: hasParallelEdge ? PARALLEL_EDGE_LABEL_OFFSET : SINGLE_EDGE_LABEL_OFFSET,
      sourceAccountNumber,
      targetAccountNumber,
      label:
        `Edge ${edge.edgeId} from ${edge.sourceNodeId} to ${edge.targetNodeId}, ` +
        `transaction #${sequence}, ${relativeTime}, synthetic accounts ` +
        `${sourceAccountNumber} to ${targetAccountNumber}, ${edge.amountBand}, ` +
        `owned by ${agencyName(edge.ownerAgencyIndex)}` +
        (present ? "" : " — unavailable in this agency view"),
    };
  });

  const ghostCount = edgeViews.filter((edge) => !edge.present).length;
  const motifOwnerName = motif.servable
    ? agencyName(motif.edges[0]?.ownerAgencyIndex ?? tenantIndex)
    : null;
  const motifAgencies = [
    ...new Set([
      ...motif.nodes.map((node) => node.agencyIndex),
      ...motif.edges.map((edge) => edge.ownerAgencyIndex),
    ]),
  ].sort((a, b) => a - b);

  const description =
    `${TYPOLOGY_LABELS[motif.typology]} motif with ${motif.nodes.length} accounts and ` +
    `${motif.edges.length} directed transfers labelled by chronological order and relative time` +
    (scope === "global"
      ? ", global view showing every agency's edges."
      : `, ${tenantName} view — ${ghostCount} edge(s) owned by other agencies are unavailable.`);

  const metrics = data.metrics;
  const isolationPositive = metrics.isolationDeltaC > 0;
  const liftSupported = metrics.armAToCLift > 0 && metrics.armAToCCiLower > 0;

  return (
    <div className="gap-2xl flex flex-col">
      <PageHeader
        title="Graph typologies & tenant isolation"
        description="How much fraud-detection lift multi-hop graph features add — and how much of it depends on seeing across tenant boundaries FraudLens does not cross."
      />

      <div
        role="note"
        className="gap-sm border-warning-deep/30 bg-warning/10 p-xl flex flex-col rounded-xl border"
      >
        <p className="text-body-sm text-ink font-semibold">
          Public synthetic offline study — not live tenant data.
        </p>
        <p className="text-body-sm text-body">
          These graph features are measured offline and serve in no scope. A static page behind
          login is not tenant-confidentiality authorization; it is safe only because the data is
          public, synthetic, aggregated, and opaque.{" "}
          <a
            href={ADR_017_HREF}
            target="_blank"
            rel="noreferrer"
            className="text-ink font-semibold underline"
          >
            ADR-017 · Graph feature serving boundary
          </a>
        </p>
      </div>

      <p className="text-body-md text-ink max-w-[70ch] font-semibold" data-testid="study-finding">
        {headlineFinding(metrics)}
      </p>

      <dl className="gap-lg grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label={`Holdout PR-AUC · ${metrics.datasetSource}`}
          value={metrics.armCPrAuc.toFixed(4)}
          hint={`Arm A baseline ${metrics.armAPrAuc.toFixed(4)}`}
        />
        <StatTile
          label="Normalized lift (Arm C)"
          value={`${metrics.armCPrAucNormalized.toFixed(1)}×`}
          hint="PR-AUC over base rate"
        />
        <StatTile
          label="Full graph lift (A→C)"
          value={signed(metrics.armAToCLift)}
          hint={
            liftSupported
              ? `95% CI [${signed(metrics.armAToCCiLower)}, ${signed(metrics.armAToCCiUpper)}]`
              : "no significant lift at 95% CI"
          }
        />
        <StatTile
          label={isolationPositive ? "Cost of isolation (Arm C)" : "Isolation delta (Arm C)"}
          value={signed(metrics.isolationDeltaC)}
          hint="global − per-tenant PR-AUC"
        />
      </dl>

      <section className="gap-lg bg-canvas p-xl flex flex-col rounded-xl">
        <div className="gap-lg flex flex-col lg:flex-row lg:items-center lg:justify-between">
          <SegmentedControl
            ariaLabel="Laundering typology"
            value={activeTypology}
            onChange={(value) => {
              setActiveTypology(value as Typology);
              setSelected(null);
            }}
            options={available.map((typology) => ({
              value: typology,
              label: TYPOLOGY_LABELS[typology],
            }))}
          />
          <SegmentedControl
            ariaLabel="Graph scope"
            value={scope}
            onChange={(value) => setScope(value as Scope)}
            options={[
              { value: "global", label: "Global" },
              { value: "tenant", label: tenantName },
            ]}
          />
        </div>

        <div role="note" className="gap-xs bg-canvas-soft p-lg flex flex-col rounded-lg">
          <p className="text-body-sm text-ink font-semibold">How to read this graph</p>
          <p className="text-caption text-body">
            Arrowheads show money direction; #1, #2, and so on show order. Each time label says how
            long after the first transfer that step occurred. Node labels show the account’s role
            and last four digits. Account numbers are synthetic display-only aliases; full numbers
            are listed in the Account key.
          </p>
        </div>

        <ResearchGraphCanvas
          title={`${TYPOLOGY_LABELS[motif.typology]} — ${scope === "global" ? "global" : tenantName} view`}
          description={description}
          width={layout.width}
          height={layout.height}
          nodeViews={nodeViews}
          edgeViews={edgeViews}
          selected={selected}
          onSelect={setSelected}
          motifAgencies={motifAgencies}
          agencyName={agencyName}
          tenantIndex={tenantIndex}
          tenantName={tenantName}
          scope={scope}
          motifServable={motif.servable}
          motifOwnerName={motifOwnerName}
          ghostCount={ghostCount}
        />
      </section>
    </div>
  );
}
