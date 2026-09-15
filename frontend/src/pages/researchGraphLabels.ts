/**
 * Summary: Deterministic graph labels, synthetic account aliases, and study finding copy.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - TYPOLOGY_LABELS:
 * - SINGLE_EDGE_LABEL_OFFSET:
 * - PARALLEL_EDGE_LABEL_OFFSET:
 * - signed:
 * - formatRelativeTime: describe an edge offset.
 * - syntheticAccountNumber: derive a display-only synthetic account alias.
 * - accountReference:
 * - accountRole: derive a node's topology role.
 * - edgePair:
 * - headlineFinding: derive sign-honest study conclusion copy.
 * - clampAgency:
 *
 * Notes:
 * - All conclusions follow measured signs rather than authored favorable prose.
 */
import type { GfpStudyData, Typology } from "../lib/gfpStudy";

export const TYPOLOGY_LABELS: Record<Typology, string> = {
  scatter_gather: "Scatter–gather",
  intra_tenant_cycle: "Intra-tenant cycle",
  cross_tenant_cycle: "Cross-tenant cycle",
};

export type Scope = "global" | "tenant";

const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const ACCOUNT_INSTITUTION_WIDTH = 6;
const ACCOUNT_GROUP_WIDTH = 4;
const ACCOUNT_INSTITUTION_BASE = 361_187;
const ACCOUNT_INSTITUTION_STEP = 111_111;
const ACCOUNT_SUFFIX_MODULUS = 10_000;
const ACCOUNT_PRODUCT_GROUPS = "3828 2049";
export const SINGLE_EDGE_LABEL_OFFSET = 16;
export const PARALLEL_EDGE_LABEL_OFFSET = 24;

type MotifEdges = GfpStudyData["motifs"][number]["edges"];

export function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}`;
}

// Shorter precision for the plain-language finding: four decimals read as noise in a sentence.
function signedShort(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

export function formatRelativeTime(offsetSeconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(offsetSeconds));
  const hours = Math.floor(totalSeconds / (SECONDS_PER_MINUTE * MINUTES_PER_HOUR));
  const minutes = Math.floor(totalSeconds / SECONDS_PER_MINUTE) % MINUTES_PER_HOUR;
  const seconds = totalSeconds % SECONDS_PER_MINUTE;

  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m later`;
  }
  if (minutes > 0) {
    return seconds > 0 ? `${minutes}m ${seconds}s later` : `${minutes}m later`;
  }
  return seconds > 0 ? `${seconds}s later` : "Starts here";
}

export function syntheticAccountNumber(agencyIndex: number, nodeIndex: number): string {
  const institution = String(
    (ACCOUNT_INSTITUTION_BASE + agencyIndex * ACCOUNT_INSTITUTION_STEP) % 1_000_000,
  ).padStart(ACCOUNT_INSTITUTION_WIDTH, "0");
  const suffix = String(nodeIndex % ACCOUNT_SUFFIX_MODULUS).padStart(ACCOUNT_GROUP_WIDTH, "0");
  return `${institution} ${ACCOUNT_PRODUCT_GROUPS} ${suffix}`;
}

export function accountReference(accountNumber: string): string {
  return `•••• ${accountNumber.slice(-ACCOUNT_GROUP_WIDTH)}`;
}

export function accountRole(nodeId: string, edges: MotifEdges): string {
  const incoming = edges.filter((edge) => edge.targetNodeId === nodeId).length;
  const outgoing = edges.filter((edge) => edge.sourceNodeId === nodeId).length;

  if (incoming === 0 && outgoing > 1) {
    return "Scatter origin";
  }
  if (incoming > 1) {
    return "Convergence";
  }
  if (incoming > 0 && outgoing > 0) {
    return "Relay";
  }
  if (outgoing > 0) {
    return "Origin";
  }
  if (incoming > 0) {
    return "Destination";
  }
  return "Account";
}

export function edgePair(sourceNodeId: string, targetNodeId: string): string {
  return [sourceNodeId, targetNodeId].sort().join("::");
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
export function headlineFinding(metrics: GfpStudyData["metrics"]): string {
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

export function clampAgency(index: number | null, count: number): number {
  if (index === null || index < 0 || index >= count) {
    return 0;
  }
  return index;
}
