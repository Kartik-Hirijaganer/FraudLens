/**
 * Summary: The streamed SAR draft view (plan §16 Phase 11 SarStream). It renders the SAR
 * narrative as it streams in token-by-token, with a blinking caret while streaming (the
 * caret animation is suppressed under reduced motion). If SAR drafting failed it shows a
 * graceful-degradation note — the risk score, explanation, and citations remain valid even
 * when the LLM step couldn't complete (§7.5 / §10.6) — and an empty pre-stream shows a
 * placeholder.
 *
 * Key classes:
 * - SarStreamProps: props (the accumulated text + streaming/failed flags).
 *
 * Key functions:
 * - SarStream: render the streaming SAR narrative, failed note, or placeholder.
 *
 * Notes:
 * - Text is whitespace-preserved and word-wrapped; it is the PHI-masked narrative built
 *   from rule hits + SHAP feature names + citations (never raw PHI, §7.8).
 */
import { cx } from "../lib/cx";
import { usePrefersReducedMotion } from "../lib/motion";
import { EmptyState } from "./feedback/EmptyState";

export interface SarStreamProps {
  text: string;
  streaming: boolean;
  failed?: boolean;
}

export function SarStream({ text, streaming, failed = false }: SarStreamProps) {
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
    <div className="bg-canvas-soft p-lg rounded-lg">
      <pre className="text-body-sm text-ink whitespace-pre-wrap break-words font-sans">
        {text}
        {streaming ? (
          <span
            aria-hidden="true"
            className={cx("text-mute", !reduced && "motion-safe:animate-pulse")}
          >
            ▍
          </span>
        ) : null}
      </pre>
    </div>
  );
}
