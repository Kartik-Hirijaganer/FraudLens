/**
 * Summary: Maps a thrown error onto a user-facing description (plan §16 Phase 11
 * `lib/errors.ts` — code -> UX). The API client throws `ApiError` carrying the stable
 * envelope `code` (e.g. `duplicate_external_id`, `admin_role_required`); this turns a
 * known code into curated copy and a criticality flag (critical errors persist as a
 * toast, §13). Unknown codes fall back to the envelope's own PHI-free `message`, and a
 * non-API failure (a dropped fetch) becomes a generic connectivity message — so an
 * internal detail, stack, or raw value is NEVER surfaced to the analyst (FraudLens
 * governance: no PHI/internals in user-visible errors).
 *
 * Key classes:
 * - ErrorDescription: the resolved {title, description, critical, code} for display.
 *
 * Key functions:
 * - describeError: turn any thrown value into a safe ErrorDescription.
 *
 * Notes:
 * - The description prefers curated copy, then the envelope message (authored PHI-free),
 *   then a status-based generic — it never echoes the raw error/stack.
 */
import { ApiError } from "./api";

export interface ErrorDescription {
  title: string;
  description: string;
  critical: boolean;
  code: string;
}

interface CodeUx {
  title: string;
  description: string;
  critical?: boolean;
}

const CODE_UX: Record<string, CodeUx> = {
  duplicate_external_id: {
    title: "Already ingested",
    description: "A transaction with that externalId already exists for this agency.",
  },
  transaction_not_found: {
    title: "Transaction not found",
    description: "It may have been removed.",
  },
  investigation_not_found: {
    title: "Investigation not found",
    description: "It may have expired.",
  },
  investigations_unavailable: {
    title: "Investigations unavailable",
    description: "The investigation service isn't ready yet. Try again shortly.",
    critical: true,
  },
  alert_not_found: { title: "Alert not found", description: "It may have been removed." },
  invalid_alert_transition: {
    title: "Action not allowed",
    description: "That action isn't valid from the alert's current status.",
  },
  invalid_sar_transition: {
    title: "Review not allowed",
    description: "That decision isn't valid from the SAR draft's current status.",
  },
  assignee_not_in_agency: {
    title: "Invalid assignee",
    description: "That user doesn't belong to this agency.",
  },
  admin_role_required: {
    title: "Admin only",
    description: "This action requires the admin role.",
  },
  insufficient_matured_labels: {
    title: "Not enough labels",
    description: "Resolve more alerts before retraining a candidate model.",
  },
  training_in_progress: {
    title: "Training already running",
    description: "A model training run is already in progress.",
  },
  invalid_model_transition: {
    title: "Promotion not allowed",
    description: "That step isn't valid from the model version's current status.",
  },
  nothing_to_rollback: {
    title: "Nothing to roll back",
    description: "There's no canary or previous deployment to restore.",
  },
  deployment_not_found: {
    title: "No deployment",
    description: "No model deployment is configured yet.",
  },
};

function genericForStatus(status: number): string {
  if (status === 401) {
    return "Please sign in again.";
  }
  if (status === 403) {
    return "You don't have access to that.";
  }
  if (status === 429) {
    return "Too many requests — please slow down.";
  }
  if (status >= 500) {
    return "The server had a problem. Please try again.";
  }
  return "Something went wrong. Please try again.";
}

export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    const curated = CODE_UX[error.code];
    const critical = curated?.critical ?? error.status >= 500;
    return {
      title: curated?.title ?? "Request failed",
      description: curated?.description ?? (error.message || genericForStatus(error.status)),
      critical,
      code: error.code,
    };
  }
  return {
    title: "Can't reach FraudLens",
    description: "Check your connection and try again.",
    critical: true,
    code: "network_error",
  };
}
