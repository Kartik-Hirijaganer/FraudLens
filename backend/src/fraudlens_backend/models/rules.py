"""Summary: Pydantic request/response models for the AML-rules CRUD surface (plan §5.3
endpoint 14, §16 Phase 4). Every model is a `CamelModel`, so the wire is camelCase while
Python stays snake_case, and `extra="forbid"` rejects unknown fields. `ruleType` reuses the
canonical `fraudlens_core.AmlRuleType` and `severity` the shared `Severity` enum (no
duplicated vocabularies, rule 5). The create body sets the immutable identity (`code`,
`ruleType`); the update body is a partial PATCH where only the fields actually sent are
applied (tracked via `model_fields_set`) and the rule's `version` is bumped server-side.
`params` is a free-form camelCase JSONB object carrying the rule's tunables (validated for
shape per rule type by the engine's defensive param parsing, not echoed as PHI).

Key classes:
- RuleCreateRequest: a new agency-scoped rule (code + type immutable thereafter).
- RuleUpdateRequest: a partial update of a rule's mutable fields (PATCH).
- RuleResponse: a persisted rule row projected onto the API surface.
- RuleListResponse: the agency's rules (its own custom overrides).

Key functions:
- (none)

Notes:
- `weight` is a Decimal and serializes as a JSON string (precision preserved), like `amount`.
- The update body intentionally omits `code`/`ruleType`: a rule's identity and dispatch type
  are fixed at creation; to change them, delete and recreate the rule.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from fraudlens_backend.db.models.enums import Severity
from fraudlens_backend.models.common import CamelModel
from fraudlens_core import AmlRuleType

_MAX_CODE_LEN = 64
_MAX_NAME_LEN = 255


class RuleCreateRequest(CamelModel):
    """A new agency-scoped rule; `code` + `ruleType` are immutable after creation."""

    code: str = Field(
        ..., min_length=1, max_length=_MAX_CODE_LEN, description="Stable per-agency rule code."
    )
    name: str = Field(..., min_length=1, max_length=_MAX_NAME_LEN, description="Rule display name.")
    description: str = Field(default="", description="Optional human-readable description.")
    rule_type: AmlRuleType = Field(..., description="Which built-in evaluator runs this rule.")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Rule-type-specific tunables (camelCase JSONB)."
    )
    severity: Severity = Field(
        default=Severity.MEDIUM, description="Ordinal severity carried through to a hit."
    )
    weight: Decimal = Field(
        default=Decimal("1.0"), gt=0, description="Aggregation weight of the rule's contribution."
    )
    enabled: bool = Field(default=True, description="Whether the rule is evaluated.")


class RuleUpdateRequest(CamelModel):
    """A partial update of a rule's mutable fields; only sent fields are applied (PATCH)."""

    name: str | None = Field(
        default=None, min_length=1, max_length=_MAX_NAME_LEN, description="New display name."
    )
    description: str | None = Field(default=None, description="New description.")
    params: dict[str, Any] | None = Field(default=None, description="Replacement tunables object.")
    severity: Severity | None = Field(default=None, description="New severity.")
    weight: Decimal | None = Field(default=None, gt=0, description="New aggregation weight.")
    enabled: bool | None = Field(default=None, description="Enable (true) or disable (false).")


class RuleResponse(CamelModel):
    """A persisted rule row projected onto the API surface."""

    rule_id: str = Field(..., description="The rule's unique id (UUID).")
    agency_id: str = Field(..., description="Owning tenant (agency) id.")
    code: str = Field(..., description="Stable per-agency rule code.")
    name: str = Field(..., description="Rule display name.")
    description: str = Field(..., description="Human-readable description.")
    rule_type: AmlRuleType = Field(..., description="The rule's evaluator type.")
    params: dict[str, Any] = Field(..., description="Rule-type-specific tunables.")
    severity: Severity = Field(..., description="Ordinal severity.")
    weight: Decimal = Field(..., description="Aggregation weight.")
    enabled: bool = Field(..., description="Whether the rule is evaluated.")
    version: int = Field(..., description="Monotonic version (bumped on each update).")
    created_at: datetime = Field(..., description="When the rule was created.")
    updated_at: datetime = Field(..., description="When the rule was last updated.")


class RuleListResponse(CamelModel):
    """The agency's own rules (its custom overrides; baseline rules are global)."""

    rules: list[RuleResponse] = Field(
        default_factory=list, description="The agency's rules, ordered by code."
    )
