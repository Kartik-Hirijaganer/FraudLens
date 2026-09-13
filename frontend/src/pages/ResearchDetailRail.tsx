/**
 * Summary: Selected-node, selected-edge, and isolation-boundary detail copy for research graphs.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - DetailBody: render selected or motif-level explanation.
 *
 * Notes:
 * - Copy distinguishes offline study partitions from runtime tenants.
 */
import type { EdgeView, GraphSelection, NodeView } from "../components/MotifGraph";
import type { Scope } from "./researchGraphLabels";

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

export function DetailBody({
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
