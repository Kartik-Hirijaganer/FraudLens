/**
 * Summary: The authenticated research page at `#/research/graph-typologies` (GFP study
 * Phase 7). It makes the tenant-isolation trade-off VISIBLE: a hero of the study's signed
 * headline metrics, a prominent "public synthetic offline study — not live tenant data"
 * banner with the ADR-017 link, three laundering-motif tabs, and a Global / current-agency
 * scope control. In the agency view, edges the current tenant owns stay solid and edges
 * owned by other agencies render as dashed "unavailable" ghosts — so the cross-tenant
 * cycle's edges visibly disappear when you switch to a single agency. Everything is driven
 * by the one committed, redacted study artifact (`lib/gfpStudy`); there is no backend call
 * and no cross-tenant query. Colour uses only wise tokens (ink / cyan / orange, never the
 * reserved primary green) plus a letter channel + legend so colour is never load-bearing.
 *
 * Key classes:
 * - ResearchProps: the committed study data + the viewer's verified agency index.
 *
 * Key functions:
 * - ADR_017_HREF: bundle the canonical ADR text into a UTF-8 browser-readable URL.
 * - Research: render the finding, metric hero, banner, motif tabs, scope control, graph, panels.
 * - RESEARCH_PATH: the canonical hash route for the research page.
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
import {
  MotifGraph,
  type EdgeView,
  type GraphSelection,
  type NodeView,
} from "../components/MotifGraph";
import { StatTile } from "../components/ui/StatTile";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { PageHeader } from "../components/ui/PageHeader";
import { agencyStyle } from "../lib/agencyStyle";
import { cx } from "../lib/cx";
import { layoutGraph } from "../lib/graphLayout";
import { TYPOLOGIES, type GfpStudyData, type Typology } from "../lib/gfpStudy";
import { paths } from "../lib/router";

function createAdrHref(): string {
  if (typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(new Blob([adr017Text], { type: "text/plain;charset=utf-8" }));
  }
  return `data:text/plain;charset=utf-8,${encodeURIComponent(adr017Text)}`;
}

export const ADR_017_HREF = createAdrHref();

const TYPOLOGY_LABELS: Record<Typology, string> = {
  scatter_gather: "Scatter–gather",
  intra_tenant_cycle: "Intra-tenant cycle",
  cross_tenant_cycle: "Cross-tenant cycle",
};

type Scope = "global" | "tenant";

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}`;
}

// Shorter precision for the plain-language finding: four decimals read as noise in a sentence.
function signedShort(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

/**
 * Build the study's headline finding as one plain sentence, DERIVED from the metrics.
 *
 * The four tiles below it lead with PR-AUC and a normalized multiple, which a non-ML reader cannot
 * turn into a conclusion. The conclusion is the relationship between two of them: the graph lift is
 * large, and almost none of it needs a cross-tenant graph. Every number here is read from the
 * artifact, and the wording follows the sign of the measured values rather than assuming the
 * favourable result — a zero or negative isolation delta is a valid outcome (ADR-017).
 */
function headlineFinding(metrics: GfpStudyData["metrics"]): string {
  const lift = `Multi-hop graph features move holdout PR-AUC by ${signedShort(metrics.armAToCLift)}`;
  if (metrics.isolationDeltaC > 0) {
    return (
      `${lift}. Only ${signedShort(metrics.isolationDeltaC)} of that depends on seeing across ` +
      `tenant boundaries — so FraudLens declines to cross them, at almost none of the benefit.`
    );
  }
  return (
    `${lift}. Restricting the graph to a single tenant costs nothing measurable ` +
    `(${signedShort(metrics.isolationDeltaC)}), so the isolation boundary is free here.`
  );
}

function clampAgency(index: number | null, count: number): number {
  if (index === null || index < 0 || index >= count) {
    return 0;
  }
  return index;
}

export interface ResearchProps {
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

  const nodeViews: NodeView[] = motif.nodes.map((node) => {
    const coords = position.get(node.nodeId) ?? { x: layout.width / 2, y: layout.height / 2 };
    return {
      id: node.nodeId,
      x: coords.x,
      y: coords.y,
      agencyIndex: node.agencyIndex,
      glyph: agencyStyle(node.agencyIndex).letter,
      label: `Node ${node.nodeId}, ${agencyName(node.agencyIndex)} (agency ${agencyStyle(node.agencyIndex).letter})`,
    };
  });

  const edgeViews: EdgeView[] = motif.edges.map((edge) => {
    const source = position.get(edge.sourceNodeId);
    const target = position.get(edge.targetNodeId);
    const present = scope === "global" || edge.ownerAgencyIndex === tenantIndex;
    return {
      id: edge.edgeId,
      x1: source?.x ?? 0,
      y1: source?.y ?? 0,
      x2: target?.x ?? 0,
      y2: target?.y ?? 0,
      ownerAgencyIndex: edge.ownerAgencyIndex,
      present,
      label:
        `Edge ${edge.edgeId} from ${edge.sourceNodeId} to ${edge.targetNodeId}, ` +
        `${edge.amountBand}, owned by ${agencyName(edge.ownerAgencyIndex)}` +
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
    `${motif.edges.length} transfers` +
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

        <div className="gap-xl flex flex-col lg:flex-row">
          <div className="bg-canvas-soft grow overflow-x-auto rounded-lg">
            <MotifGraph
              titleId="motif-graph-title"
              descId="motif-graph-desc"
              title={`${TYPOLOGY_LABELS[motif.typology]} — ${scope === "global" ? "global" : tenantName} view`}
              description={description}
              width={layout.width}
              height={layout.height}
              nodes={nodeViews}
              edges={edgeViews}
              selected={selected}
              onSelect={setSelected}
            />
          </div>

          <aside className="gap-lg flex shrink-0 flex-col lg:w-[280px]">
            <div className="gap-sm flex flex-col">
              <h2 className="text-caption text-mute font-semibold uppercase tracking-wide">
                Agencies
              </h2>
              <ul className="gap-xs flex flex-col">
                {motifAgencies.map((index) => (
                  <li key={index} className="gap-sm text-body-sm text-body flex items-center">
                    <span
                      aria-hidden="true"
                      className={cx("size-md rounded-sm", agencyStyle(index).swatch)}
                    />
                    <span className="text-ink font-semibold">{agencyStyle(index).letter}</span>
                    <span>{agencyName(index)}</span>
                  </li>
                ))}
              </ul>
              <p className="text-caption text-mute">
                {scope === "global"
                  ? "Global scope shows every agency's edges as solid."
                  : `Solid = owned by ${tenantName} · dashed = unavailable to this tenant.`}
              </p>
              {/* Anchors the study to the app the reader is signed into WITHOUT claiming a
                  partition is a tenant: these are offline analysis partitions, and the runtime
                  demo agency declares which one it mirrors (`agency.research_partition_key`). */}
              <p className="text-caption text-mute" data-testid="partition-anchor">
                <span className="text-ink font-semibold">
                  {agencyStyle(tenantIndex).letter} · {tenantName}
                </span>{" "}
                is the offline study partition the agency you are signed into mirrors. Partitions
                are an analysis concept — only one runtime tenant exists.
              </p>
            </div>

            <div className="gap-sm border-canvas-soft p-lg flex flex-col rounded-lg border">
              <h2 className="text-caption text-mute font-semibold uppercase tracking-wide">
                Detail
              </h2>
              <DetailBody
                selected={selected}
                nodeViews={nodeViews}
                edgeViews={edgeViews}
                motifServable={motif.servable}
                motifOwnerName={motifOwnerName}
                scope={scope}
                tenantName={tenantName}
                ghostCount={ghostCount}
              />
            </div>
          </aside>
        </div>

        <details className="text-body-sm text-body">
          <summary className="text-ink cursor-pointer font-semibold">
            Text alternative — nodes and edges
          </summary>
          <div className="gap-lg mt-md flex flex-col lg:flex-row">
            <table className="grow text-left">
              <caption className="text-caption text-mute mb-xs text-left">Accounts</caption>
              <thead>
                <tr className="text-caption text-mute">
                  <th scope="col" className="pr-lg">
                    Node
                  </th>
                  <th scope="col">Agency</th>
                </tr>
              </thead>
              <tbody>
                {nodeViews.map((node) => (
                  <tr key={node.id}>
                    <td className="pr-lg">{node.id}</td>
                    <td>{agencyName(node.agencyIndex)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <table className="grow text-left">
              <caption className="text-caption text-mute mb-xs text-left">Transfers</caption>
              <thead>
                <tr className="text-caption text-mute">
                  <th scope="col" className="pr-lg">
                    Edge
                  </th>
                  <th scope="col" className="pr-lg">
                    Owner
                  </th>
                  <th scope="col">Visible</th>
                </tr>
              </thead>
              <tbody>
                {edgeViews.map((edge) => (
                  <tr key={edge.id}>
                    <td className="pr-lg">{edge.id}</td>
                    <td className="pr-lg">{agencyName(edge.ownerAgencyIndex)}</td>
                    <td>{edge.present ? "yes" : "unavailable"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </div>
  );
}

interface DetailBodyProps {
  selected: GraphSelection;
  nodeViews: NodeView[];
  edgeViews: EdgeView[];
  motifServable: boolean;
  motifOwnerName: string | null;
  scope: Scope;
  tenantName: string;
  ghostCount: number;
}

function DetailBody({
  selected,
  nodeViews,
  edgeViews,
  motifServable,
  motifOwnerName,
  scope,
  tenantName,
  ghostCount,
}: DetailBodyProps) {
  if (selected?.kind === "node") {
    const node = nodeViews.find((candidate) => candidate.id === selected.id);
    if (node) {
      return <p className="text-body-sm text-body">{node.label}</p>;
    }
  }
  if (selected?.kind === "edge") {
    const edge = edgeViews.find((candidate) => candidate.id === selected.id);
    if (edge) {
      return <p className="text-body-sm text-body">{edge.label}</p>;
    }
  }
  if (motifServable && scope === "tenant") {
    return (
      <p className="text-body-sm text-body">
        {ghostCount === 0
          ? `Every edge is owned by ${tenantName}; this motif survives the isolation boundary.`
          : `Every edge is owned by ${motifOwnerName ?? "one agency"}; ${tenantName} cannot see this motif in its isolated graph.`}
      </p>
    );
  }
  return (
    <p className="text-body-sm text-body">
      {motifServable
        ? `Every edge is owned by ${motifOwnerName ?? "one agency"}; the motif is servable within that single agency.`
        : "This motif spans multiple tenants, so no single agency can see it — the point of the isolation boundary. Select a node or edge for detail."}
    </p>
  );
}

// Keep the canonical route href importable alongside the page (rule 5: one source of truth).
export const RESEARCH_PATH = paths.researchGraphTypologies;
