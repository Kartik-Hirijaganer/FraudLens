/**
 * Summary: The agency colour + letter channel for the research page's motif graphs and
 * legend (GFP study Phase 7). Agencies are drawn in the wise `ink` / `accent-cyan` /
 * `accent-orange` tokens — never the reserved primary green — and EVERY agency also
 * carries a letter (A, B, C, …) so colour is never the sole way to tell tenants apart
 * (DESIGN.md accessibility). Both the SVG (fill/stroke tokens) and the legend (a
 * background swatch) read from one source here so they can never disagree.
 *
 * Key classes:
 * - AgencyStyle: the token class names + letter for one agency index.
 *
 * Key functions:
 * - agencyStyle: resolve the stable style for an agency index (palette cycles; letter counts up).
 *
 * Notes:
 * - Class names are literal strings so Tailwind's content scan keeps them; never build them
 *   dynamically from the index (that would purge the utilities).
 */
export interface AgencyStyle {
  letter: string;
  nodeFill: string;
  nodeText: string;
  edgeStroke: string;
  swatch: string;
}

const PALETTE: readonly Omit<AgencyStyle, "letter">[] = [
  { nodeFill: "fill-ink", nodeText: "fill-canvas", edgeStroke: "stroke-ink", swatch: "bg-ink" },
  {
    nodeFill: "fill-accent-cyan",
    nodeText: "fill-ink",
    edgeStroke: "stroke-accent-cyan",
    swatch: "bg-accent-cyan",
  },
  {
    nodeFill: "fill-accent-orange",
    nodeText: "fill-ink",
    edgeStroke: "stroke-accent-orange",
    swatch: "bg-accent-orange",
  },
];

export function agencyStyle(index: number): AgencyStyle {
  const base = PALETTE[index % PALETTE.length];
  return { ...base, letter: String.fromCharCode(65 + index) };
}
