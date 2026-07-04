"""Summary: The FraudLens error-code catalog (plan §16 Phase 3). One table maps every
stable business error `code` to its HTTP status and a fixed, PHI-free user message, so the
API surface, the docs (`docs/reference/configuration.md`), and the tests share a single
source of truth (no duplication, rule 5). Handlers raise `AppError(code, ...)` instead of a
bare `HTTPException`, and the registered handler (api/errors.py) renders the catalog entry
into the standard envelope `{code, message, details, requestId}` — so the machine-readable
code (e.g. `duplicate_external_id`) is preserved rather than collapsing to a generic status
name. `details` carries only field/reason pairs, never raw input, keeping PHI out of errors.

Key classes:
- ErrorSpec: one catalog entry — code, HTTP status, and a fixed user message.
- AppError: a raisable error referencing a catalog code (+ optional safe details).

Key functions:
- get_error_spec: look up the ErrorSpec for a code (raises KeyError on an unknown code).

Notes:
- Messages are static and value-free; per-field context goes in `details` as {field,
  message} pairs (the same shape the validation handler emits), never echoed input.
- This module imports nothing from the API layer, so api/errors.py can import the catalog
  to render AppError without an import cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorSpec(BaseModel):
    """One catalog entry: a stable code, its HTTP status, and a fixed user message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(..., description="Stable machine-readable error code.")
    http_status: int = Field(..., ge=400, le=599, description="HTTP status for this code.")
    message: str = Field(..., description="Fixed, PHI-free human-readable message.")


# The business error catalog (code -> {httpStatus, userMessage}): Phase 3 ingestion +
# Phase 4 rules CRUD. Generic transport errors (401/403/404/422 from auth, tenancy, and
# request validation) are rendered by the status-driven handler in api/errors.py and are
# not duplicated here.
ERROR_CATALOG: dict[str, ErrorSpec] = {
    spec.code: spec
    for spec in (
        ErrorSpec(
            code="duplicate_external_id",
            http_status=409,
            message="A transaction with this externalId already exists for this agency.",
        ),
        ErrorSpec(
            code="transaction_not_found",
            http_status=404,
            message="No transaction with that id exists for this agency.",
        ),
        ErrorSpec(
            code="payload_too_large",
            http_status=413,
            message="The uploaded file exceeds the maximum allowed size.",
        ),
        ErrorSpec(
            code="too_many_rows",
            http_status=413,
            message="The upload contains more rows than the per-request limit allows.",
        ),
        ErrorSpec(
            code="batch_too_large",
            http_status=413,
            message="The batch contains more transactions than the per-request limit allows.",
        ),
        ErrorSpec(
            code="empty_payload",
            http_status=422,
            message="The request contained no transactions to ingest.",
        ),
        ErrorSpec(
            code="invalid_csv",
            http_status=422,
            message="The uploaded file could not be parsed as CSV.",
        ),
        ErrorSpec(
            code="unsupported_content_type",
            http_status=415,
            message="Upload the file as text/csv.",
        ),
        ErrorSpec(
            code="rule_not_found",
            http_status=404,
            message="No rule with that id exists for this agency.",
        ),
        ErrorSpec(
            code="duplicate_rule_code",
            http_status=409,
            message="A rule with this code already exists for this agency.",
        ),
        ErrorSpec(
            code="model_version_not_found",
            http_status=404,
            message="No model version with that id exists in the registry.",
        ),
        ErrorSpec(
            code="investigation_not_found",
            http_status=404,
            message="No investigation run with that id exists for this agency.",
        ),
        ErrorSpec(
            code="investigations_unavailable",
            http_status=503,
            message="The investigation service is not available (database not configured).",
        ),
        # --- Phase 9: alerts & review workflow (endpoints 9-12) ---
        ErrorSpec(
            code="alert_not_found",
            http_status=404,
            message="No alert with that id exists for this agency.",
        ),
        ErrorSpec(
            code="invalid_alert_transition",
            http_status=409,
            message="That action is not allowed from the alert's current status.",
        ),
        ErrorSpec(
            code="sar_draft_not_found",
            http_status=404,
            message="No SAR draft exists for this alert's investigation.",
        ),
        ErrorSpec(
            code="invalid_sar_transition",
            http_status=409,
            message="That review decision is not allowed from the SAR draft's current status.",
        ),
        ErrorSpec(
            code="sar_not_regenerable",
            http_status=409,
            message="This run has no completed analysis to regenerate a SAR draft from.",
        ),
        ErrorSpec(
            code="assignee_not_in_agency",
            http_status=403,
            message="The requested assignee does not belong to this agency.",
        ),
        ErrorSpec(
            code="acting_user_required",
            http_status=401,
            message="A verified acting user is required for this action.",
        ),
        # --- Phase 10: model lifecycle / MLOps (endpoints 19-26, admin) ---
        ErrorSpec(
            code="admin_role_required",
            http_status=403,
            message="This action requires the admin role.",
        ),
        ErrorSpec(
            code="insufficient_matured_labels",
            http_status=422,
            message="Not enough matured reviewed labels to train a candidate model yet.",
        ),
        ErrorSpec(
            code="training_in_progress",
            http_status=409,
            message="A model training run is already in progress.",
        ),
        ErrorSpec(
            code="job_submission_failed",
            http_status=503,
            message="The background job could not be started. Retry shortly.",
        ),
        ErrorSpec(
            code="invalid_model_transition",
            http_status=409,
            message="That action is not allowed from the model version's current status.",
        ),
        ErrorSpec(
            code="nothing_to_rollback",
            http_status=409,
            message="There is no canary or previous deployment to roll back to.",
        ),
        ErrorSpec(
            code="deployment_not_found",
            http_status=404,
            message="No model deployment is configured.",
        ),
        # --- Phase 13: per-route rate limiting (api/deps.rate_limit). The gateway edge builds
        # its own 429 envelope inline at the ASGI layer; this entry backs the AppError raised by
        # the per-route dependency so both paths surface the same machine-readable code. ---
        ErrorSpec(
            code="rate_limited",
            http_status=429,
            message="Too many requests; slow down and retry shortly.",
        ),
        # --- Admin runtime/dev utilities. Dev routes are production-disabled by design. ---
        ErrorSpec(
            code="dev_utility_disabled",
            http_status=403,
            message="Developer utility routes are disabled in production.",
        ),
    )
}


class AppError(Exception):
    """A raisable error that references a catalog code and optional safe details."""

    def __init__(self, code: str, *, details: list[dict[str, str]] | None = None) -> None:
        """Store the catalog `code` and optional field/message detail pairs."""
        self.code = code
        self.details = details
        super().__init__(code)


def get_error_spec(code: str) -> ErrorSpec:
    """Return the ErrorSpec for a code (raises KeyError if the code is not in the catalog)."""
    return ERROR_CATALOG[code]
