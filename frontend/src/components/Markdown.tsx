/**
 * Summary: A tiny, dependency-free Markdown renderer for the model-authored SAR narrative
 * (and any other markdown the API returns). It parses a safe subset — `#`/`##`/`###`
 * headings, `**bold**` inline spans, blank-line-separated paragraphs, and `-`/`*` bullet
 * lists — into React elements so the draft reads as formatted prose instead of showing raw
 * `#` and `**` markers. It builds nodes directly (never `dangerouslySetInnerHTML`), so the
 * PHI-masked content can't inject markup, and every style comes from design tokens.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - Markdown: render a markdown string as themed React elements.
 *
 * Notes:
 * - Deliberately minimal (no links/images/tables/code) — the SAR draft only uses headings,
 *   bold, paragraphs, and bullets; anything else renders as plain text.
 */
import type { ReactNode } from "react";

import { cx } from "../lib/cx";

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] };

const HEADING = /^(#{1,3})\s+(.*)$/;
const BULLET = /^[-*]\s+(.*)$/;

function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  let list: string[] | null = null;
  const flushList = (): void => {
    if (list) {
      blocks.push({ kind: "list", items: list });
      list = null;
    }
  };
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flushList();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flushList();
      blocks.push({ kind: "heading", level: heading[1].length as 1 | 2 | 3, text: heading[2] });
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      list ??= [];
      list.push(bullet[1]);
      continue;
    }
    flushList();
    blocks.push({ kind: "paragraph", text: line.trim() });
  }
  flushList();
  return blocks;
}

// Split on `**` so odd-indexed segments render bold; an unclosed run (mid-stream) simply
// bolds its tail until the closing `**` arrives.
function renderInline(text: string): ReactNode[] {
  return text.split("**").map((segment, index) =>
    index % 2 === 1 ? (
      <strong key={index} className="text-ink font-semibold">
        {segment}
      </strong>
    ) : (
      <span key={index}>{segment}</span>
    ),
  );
}

const HEADING_CLASSES: Record<1 | 2 | 3, string> = {
  1: "text-display-xs text-ink",
  2: "text-body-lg text-ink font-semibold",
  3: "text-body-md text-ink font-semibold",
};

interface MarkdownProps {
  text: string;
  className?: string;
}

export function Markdown({ text, className }: MarkdownProps) {
  const blocks = parseBlocks(text);
  return (
    <div className={cx("gap-md flex flex-col", className)}>
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          const Tag = `h${block.level}` as const satisfies "h1" | "h2" | "h3";
          return (
            <Tag key={index} className={HEADING_CLASSES[block.level]}>
              {renderInline(block.text)}
            </Tag>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={index} className="gap-xxs pl-lg flex list-disc flex-col">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex} className="text-body-sm text-ink">
                  {renderInline(item)}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={index} className="text-body-sm text-ink">
            {renderInline(block.text)}
          </p>
        );
      })}
    </div>
  );
}
