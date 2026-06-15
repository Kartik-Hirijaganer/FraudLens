/**
 * Summary: The regulatory-citations panel (plan §16 Phase 11 RagPanel). It lists the
 * grounded FinCEN/BSA citations the SAR relied on — title, source, citation id, and an
 * escaped snippet — and notes the retrieval mode (semantic / keyword fallback / empty) so
 * the analyst can see how the citations were found. Renders an EmptyState when retrieval
 * returned nothing (graceful degradation, §10.6).
 *
 * Key classes:
 * - RagPanelProps: props (citations + optional retrieval mode).
 *
 * Key functions:
 * - RagPanel: render the citations list (or an empty state).
 *
 * Notes:
 * - Citation snippets are pre-escaped upstream (RAG-as-data, §8.1); this only displays them.
 */
import { humanize } from "../lib/format";
import type { RegulationCitation } from "../lib/investigation";
import { Badge } from "./ui/Badge";
import { EmptyState } from "./feedback/EmptyState";

const MODE_LABELS: Record<string, string> = {
  vector: "Retrieved via semantic search",
  lexical: "Retrieved via keyword fallback",
  empty: "No regulations retrieved",
};

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? humanize(mode);
}

export interface RagPanelProps {
  citations: RegulationCitation[];
  mode?: string;
}

export function RagPanel({ citations, mode }: RagPanelProps) {
  if (citations.length === 0) {
    return (
      <EmptyState
        title="No citations"
        description={mode ? modeLabel(mode) : "Citations appear once retrieval completes."}
      />
    );
  }
  return (
    <div className="gap-md flex flex-col">
      {mode ? <p className="text-caption text-mute">{modeLabel(mode)}</p> : null}
      <ul className="gap-md flex flex-col">
        {citations.map((citation, index) => (
          <li
            key={`${citation.citation}-${index}`}
            className="gap-xs bg-canvas-soft p-lg flex flex-col rounded-lg"
          >
            <div className="gap-md flex items-center justify-between">
              <span className="text-body-md text-ink font-semibold">{citation.title}</span>
              <Badge tone="neutral">{citation.source}</Badge>
            </div>
            <span className="text-caption text-mute">{citation.citation}</span>
            <p className="text-body-sm text-body">{citation.snippet}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
