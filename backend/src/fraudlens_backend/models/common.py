"""Summary: Shared Pydantic models for the API surface. CamelModel is the base
for every request/response model: it emits camelCase JSON (FraudLens casing rule)
while keeping snake_case Python attributes, and forbids unknown fields. The FraudLens
error envelope (ErrorResponse) and the tenant context (TenantContext) live here so
every handler and exception handler shares one definition (no duplication, rule 5).

Key classes:
- CamelModel: base model with a camelCase alias generator and extra="forbid".
- ErrorResponse: the FraudLens error envelope {code, message, details, requestId}.
- TenantContext: API-surface tenant identity (agencyId) for authenticated callers.

Key functions:
- (none)

Notes:
- ErrorResponse.details carries only {field, message} pairs — never raw input
  values — so PHI is not echoed back through error bodies (FraudLens governance).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model: camelCase JSON aliases, populate-by-name, no extra fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ErrorResponse(CamelModel):
    """The FraudLens error envelope returned by every error path."""

    code: str = Field(..., description="Stable machine-readable error code.")
    message: str = Field(..., description="Human-readable, PHI-free summary.")
    details: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional field/message pairs; never contains raw input values.",
    )
    request_id: str = Field(
        ...,
        description="Correlation id echoed from the request-id header.",
    )


class TenantContext(CamelModel):
    """Authenticated tenant identity resolved from the verified agency_id claim."""

    agency_id: str = Field(
        ...,
        min_length=1,
        description="Active tenant (agency) id from the verified JWT claim.",
    )
