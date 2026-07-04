/**
 * Summary: The streamed SAR draft view (plan §16 Phase 11 SarStream). It renders the SAR
 * narrative — model-authored markdown — as formatted prose via `Markdown` (headings, bold,
 * paragraphs, bullets), streaming in token-by-token with a blinking caret while streaming
 * (suppressed under reduced motion). While a regeneration is in flight the current draft
 * dims and pulses so the analyst sees it being reworked. If SAR drafting failed it shows a
 * graceful-degradation note — the risk score, explanation, and citations remain valid even
 * when the LLM step couldn't complete (§7.5 / §10.6) — and an empty pre-stream shows a
 * placeholder.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - SarStream: render the streaming/formatted SAR narrative, failed note, or placeholder.
 *
 * Notes:
 * - The narrative is the PHI-masked draft built from rule hits + SHAP feature names +
 *   citations (never raw PHI, §7.8); `Markdown` builds nodes only, so it can't inject markup.
 */
import { cx } from "../lib/cx";
import { usePrefersReducedMotion } from "../lib/motion";
import { Markdown } from "./Markdown";
import { EmptyState } from "./feedback/EmptyState";

interface SarStreamProps {
  text: string;
  streaming: boolean;
  failed?: boolean;
  regenerating?: boolean;
}

export function SarStream({
  text,
  streaming,
  failed = false,
  regenerating = false,
}: SarStreamProps) {
  const reduced = usePrefersReducedMotion();
  if (failed) {
    return (
      <div className="bg-canvas-soft p-lg rounded-lg">
        <p className="text-body-sm text-warning-deep">
          SAR drafting was unavailable. The risk score, explanation, and citations above are still
          valid — a draft can be retried later.
        </p>
      </div>
    );
  }
  if (!text && !streaming) {
    return (
      <EmptyState
        title="No SAR draft yet"
        description="The draft streams in once the investigation reaches the SAR step."
      />
    );
  }
  return (
    <div
      aria-busy={regenerating || undefined}
      className={cx(
        "bg-canvas-soft p-lg rounded-lg",
        regenerating && "opacity-60 motion-safe:animate-pulse",
      )}
    >
      <Markdown text={text} />
      {streaming ? (
        <span
          aria-hidden="true"
          className={cx("text-mute", !reduced && "motion-safe:animate-pulse")}
        >
          ▍
        </span>
      ) : null}
    </div>
  );
}
