/**
 * Summary: The typed parser + contract for the committed offline GFP tenant-isolation
 * study visual data (GFP study Phase 7). It mirrors the Python `CuratedVisualData`
 * boundary model: opaque motif nodes/edges, agency names, the SHA-256 of the study
 * report they were curated from, and the signed headline metrics the research page's
 * hero renders. `parseStudyData` validates a raw JSON value STRICTLY — the same
 * invariants the Pydantic model enforces (declared-node references, in-range agency
 * indices, single-owner servability, ordered interval) — and throws on any malformed
 * or drifted payload, so a bad committed artifact fails the build rather than rendering
 * a broken or misleading page (plan Phase 8: "missing data fails the build, no
 * placeholders"). No network call; the data is a build-time static import.
 *
 * Key classes:
 * - StudyMotifNode: one opaque curated node (id + owning agency index).
 * - StudyMotifEdge: one opaque curated edge (relative offset, amount band, owner).
 * - StudyMotif: one curated typology exemplar (nodes, edges, servability).
 * - StudyHighlightMetrics: the signed headline numbers the hero renders.
 * - GfpStudyData: the whole committed payload the research page consumes.
 *
 * Key functions:
 * - TYPOLOGIES: the three laundering typologies the study curates, in display order.
 * - parseStudyData: validate an unknown JSON value into a typed GfpStudyData (throws on drift).
 *
 * Notes:
 * - The records are already redacted upstream (amount BANDS + RELATIVE offsets, opaque ids,
 *   agency INDEX only) — this module never sees raw tokens/amounts/timestamps/labels.
 * - Validation is intentionally strict and total: any shape the page could not render safely
 *   is a hard error, never a silent default.
 */
export const TYPOLOGIES = ["scatter_gather", "intra_tenant_cycle", "cross_tenant_cycle"] as const;

export type Typology = (typeof TYPOLOGIES)[number];

export interface StudyMotifNode {
  nodeId: string;
  agencyIndex: number;
}

export interface StudyMotifEdge {
  edgeId: string;
  sourceNodeId: string;
  targetNodeId: string;
  timeOffsetS: number;
  amountBand: string;
  ownerAgencyIndex: number;
}

export interface StudyMotif {
  motifId: string;
  typology: Typology;
  nodes: StudyMotifNode[];
  edges: StudyMotifEdge[];
  servable: boolean;
}

export interface StudyHighlightMetrics {
  datasetSource: string;
  armAPrAuc: number;
  armCPrAuc: number;
  armCPrAucNormalized: number;
  armAToCLift: number;
  armAToCCiLower: number;
  armAToCCiUpper: number;
  isolationDeltaC: number;
}

export interface GfpStudyData {
  reportSha256: string;
  metrics: StudyHighlightMetrics;
  agencyNames: string[];
  motifs: StudyMotif[];
}

class StudyDataError extends Error {
  constructor(message: string) {
    super(`invalid GFP study data: ${message}`);
    this.name = "StudyDataError";
  }
}

function asRecord(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new StudyDataError(`${where} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, where: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new StudyDataError(`${where} must be a non-empty string`);
  }
  return value;
}

function asFiniteNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new StudyDataError(`${where} must be a finite number`);
  }
  return value;
}

function asAgencyIndex(value: unknown, where: string, agencyCount: number): number {
  const index = asFiniteNumber(value, where);
  if (!Number.isInteger(index) || index < 0 || index >= agencyCount) {
    throw new StudyDataError(
      `${where} (${index}) must be an integer in [0, ${agencyCount}) — data drifted from the agency list`,
    );
  }
  return index;
}

function asBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") {
    throw new StudyDataError(`${where} must be a boolean`);
  }
  return value;
}

function parseMetrics(value: unknown): StudyHighlightMetrics {
  const raw = asRecord(value, "metrics");
  const metrics: StudyHighlightMetrics = {
    datasetSource: asString(raw.datasetSource, "metrics.datasetSource"),
    armAPrAuc: asFiniteNumber(raw.armAPrAuc, "metrics.armAPrAuc"),
    armCPrAuc: asFiniteNumber(raw.armCPrAuc, "metrics.armCPrAuc"),
    armCPrAucNormalized: asFiniteNumber(raw.armCPrAucNormalized, "metrics.armCPrAucNormalized"),
    armAToCLift: asFiniteNumber(raw.armAToCLift, "metrics.armAToCLift"),
    armAToCCiLower: asFiniteNumber(raw.armAToCCiLower, "metrics.armAToCCiLower"),
    armAToCCiUpper: asFiniteNumber(raw.armAToCCiUpper, "metrics.armAToCCiUpper"),
    isolationDeltaC: asFiniteNumber(raw.isolationDeltaC, "metrics.isolationDeltaC"),
  };
  if (metrics.armAToCCiLower > metrics.armAToCCiUpper) {
    throw new StudyDataError("metrics A->C interval lower bound exceeds its upper bound");
  }
  return metrics;
}

function parseNode(value: unknown, where: string, agencyCount: number): StudyMotifNode {
  const raw = asRecord(value, where);
  return {
    nodeId: asString(raw.nodeId, `${where}.nodeId`),
    agencyIndex: asAgencyIndex(raw.agencyIndex, `${where}.agencyIndex`, agencyCount),
  };
}

function parseEdge(value: unknown, where: string, agencyCount: number): StudyMotifEdge {
  const raw = asRecord(value, where);
  const timeOffsetS = asFiniteNumber(raw.timeOffsetS, `${where}.timeOffsetS`);
  if (timeOffsetS < 0) {
    throw new StudyDataError(`${where}.timeOffsetS must be >= 0 (relative offset)`);
  }
  return {
    edgeId: asString(raw.edgeId, `${where}.edgeId`),
    sourceNodeId: asString(raw.sourceNodeId, `${where}.sourceNodeId`),
    targetNodeId: asString(raw.targetNodeId, `${where}.targetNodeId`),
    timeOffsetS,
    amountBand: asString(raw.amountBand, `${where}.amountBand`),
    ownerAgencyIndex: asAgencyIndex(raw.ownerAgencyIndex, `${where}.ownerAgencyIndex`, agencyCount),
  };
}

function parseMotif(value: unknown, index: number, agencyCount: number): StudyMotif {
  const where = `motifs[${index}]`;
  const raw = asRecord(value, where);
  const typology = asString(raw.typology, `${where}.typology`);
  if (!(TYPOLOGIES as readonly string[]).includes(typology)) {
    throw new StudyDataError(`${where}.typology '${typology}' is not a known typology`);
  }
  if (!Array.isArray(raw.nodes) || raw.nodes.length < 2) {
    throw new StudyDataError(`${where}.nodes must list at least two nodes`);
  }
  if (!Array.isArray(raw.edges) || raw.edges.length < 1) {
    throw new StudyDataError(`${where}.edges must list at least one edge`);
  }
  const nodes = raw.nodes.map((node, i) => parseNode(node, `${where}.nodes[${i}]`, agencyCount));
  const edges = raw.edges.map((edge, i) => parseEdge(edge, `${where}.edges[${i}]`, agencyCount));
  const nodeIds = new Set(nodes.map((node) => node.nodeId));
  for (const edge of edges) {
    if (!nodeIds.has(edge.sourceNodeId) || !nodeIds.has(edge.targetNodeId)) {
      throw new StudyDataError(`${where} edge '${edge.edgeId}' references an undeclared node`);
    }
  }
  const servable = asBoolean(raw.servable, `${where}.servable`);
  const owners = new Set(edges.map((edge) => edge.ownerAgencyIndex));
  if (servable && owners.size > 1) {
    throw new StudyDataError(`${where} spans multiple owners and cannot be servable`);
  }
  return {
    motifId: asString(raw.motifId, `${where}.motifId`),
    typology: typology as Typology,
    nodes,
    edges,
    servable,
  };
}

export function parseStudyData(value: unknown): GfpStudyData {
  const raw = asRecord(value, "study data");
  const reportSha256 = asString(raw.reportSha256, "reportSha256");
  if (!/^[0-9a-f]{64}$/.test(reportSha256)) {
    throw new StudyDataError("reportSha256 must be a 64-character hex digest");
  }
  if (!Array.isArray(raw.agencyNames) || raw.agencyNames.length < 1) {
    throw new StudyDataError("agencyNames must list at least one agency");
  }
  const agencyNames = raw.agencyNames.map((name, i) => asString(name, `agencyNames[${i}]`));
  if (!Array.isArray(raw.motifs) || raw.motifs.length < 1) {
    throw new StudyDataError("motifs must list at least one curated motif");
  }
  const motifs = raw.motifs.map((motif, i) => parseMotif(motif, i, agencyNames.length));
  return {
    reportSha256,
    metrics: parseMetrics(raw.metrics),
    agencyNames,
    motifs,
  };
}
