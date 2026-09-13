/**
 * Summary: Accessible graph canvas, legends, account key, detail rail, and text alternative.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - ResearchGraphCanvas: render the interactive graph and equivalent tables.
 *
 * Notes:
 * - Ownership remains visible through style, text, and the full tabular alternative.
 */
import type { Dispatch, SetStateAction } from "react";

import {
  MotifGraph,
  type EdgeView,
  type GraphSelection,
  type NodeView,
} from "../components/MotifGraph";
import { agencyStyle } from "../lib/agencyStyle";
import { cx } from "../lib/cx";
import { DetailBody } from "./ResearchDetailRail";
import type { Scope } from "./researchGraphLabels";

interface ResearchGraphCanvasProps {
  title: string;
  description: string;
  width: number;
  height: number;
  nodeViews: NodeView[];
  edgeViews: EdgeView[];
  selected: GraphSelection;
  onSelect: Dispatch<SetStateAction<GraphSelection>>;
  motifAgencies: number[];
  agencyName: (index: number) => string;
  tenantIndex: number;
  tenantName: string;
  scope: Scope;
  motifServable: boolean;
  motifOwnerName: string | null;
  ghostCount: number;
}

export function ResearchGraphCanvas({
  title,
  description,
  width,
  height,
  nodeViews,
  edgeViews,
  selected,
  onSelect,
  motifAgencies,
  agencyName,
  tenantIndex,
  tenantName,
  scope,
  motifServable,
  motifOwnerName,
  ghostCount,
}: ResearchGraphCanvasProps) {
  return (
    <>
      <div className="gap-xl flex flex-col lg:flex-row">
        <div className="bg-canvas-soft grow overflow-x-auto rounded-lg">
          <MotifGraph
            titleId="motif-graph-title"
            descId="motif-graph-desc"
            title={title}
            description={description}
            width={width}
            height={height}
            nodes={nodeViews}
            edges={edgeViews}
            selected={selected}
            onSelect={onSelect}
          />
        </div>

        <aside className="gap-lg flex shrink-0 flex-col lg:w-[280px]">
          <div className="gap-sm flex flex-col">
            <h2 className="text-caption text-mute font-semibold uppercase tracking-wide">
              Agencies
            </h2>
            <ul aria-label="Agencies" className="gap-xs flex flex-col">
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
                ? "Arrowheads show money direction; global scope shows every agency's transfer as solid."
                : `Arrowheads show money direction · solid = owned by ${tenantName} · dashed = unavailable to this tenant.`}
            </p>
            {/* Anchors the study to the app the reader is signed into WITHOUT claiming a
                  partition is a tenant: these are offline analysis partitions, and the runtime
                  demo agency declares which one it mirrors (`agency.research_partition_key`). */}
            <p className="text-caption text-mute" data-testid="partition-anchor">
              <span className="text-ink font-semibold">
                {agencyStyle(tenantIndex).letter} · {tenantName}
              </span>{" "}
              is the offline study partition the agency you are signed into mirrors. Partitions are
              an analysis concept — only one runtime tenant exists.
            </p>
          </div>

          <div className="gap-sm flex flex-col">
            <h2 className="text-caption text-mute font-semibold uppercase tracking-wide">
              Account key
            </h2>
            <ul aria-label="Account key" className="gap-sm flex flex-col">
              {nodeViews.map((node) => (
                <li key={node.id} className="bg-canvas-soft p-sm rounded-sm">
                  <div className="gap-sm text-caption text-body flex items-center">
                    <span className="text-ink font-semibold">
                      {agencyStyle(node.agencyIndex).letter}
                    </span>
                    <span className="grow">{node.role}</span>
                    <span className="text-mute font-mono">{node.accountReference}</span>
                  </div>
                  <p className="text-caption text-ink mt-xs font-mono">{node.accountNumber}</p>
                </li>
              ))}
            </ul>
          </div>

          <div className="gap-sm border-canvas-soft p-lg flex flex-col rounded-lg border">
            <h2 className="text-caption text-mute font-semibold uppercase tracking-wide">Detail</h2>
            <DetailBody
              selected={selected}
              nodeViews={nodeViews}
              edgeViews={edgeViews}
              motifServable={motifServable}
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
                <th scope="col" className="pr-lg">
                  Synthetic account
                </th>
                <th scope="col" className="pr-lg">
                  Role
                </th>
                <th scope="col">Agency</th>
              </tr>
            </thead>
            <tbody>
              {nodeViews.map((node) => (
                <tr key={node.id}>
                  <td className="pr-lg">{node.id}</td>
                  <td className="pr-lg whitespace-nowrap font-mono">{node.accountNumber}</td>
                  <td className="pr-lg">{node.role}</td>
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
                  Order / time
                </th>
                <th scope="col" className="pr-lg">
                  From
                </th>
                <th scope="col" className="pr-lg">
                  To
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
                  <td className="pr-lg whitespace-nowrap">
                    #{edge.sequence} · {edge.relativeTime}
                  </td>
                  <td className="pr-lg whitespace-nowrap font-mono">{edge.sourceAccountNumber}</td>
                  <td className="pr-lg whitespace-nowrap font-mono">{edge.targetAccountNumber}</td>
                  <td className="pr-lg">{agencyName(edge.ownerAgencyIndex)}</td>
                  <td>{edge.present ? "yes" : "unavailable"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
