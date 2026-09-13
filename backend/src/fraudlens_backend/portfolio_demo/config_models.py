"""Summary: Validated leaf models for the configured portfolio demo story.

Key classes:
- PortfolioDemoConfigError: report safe configuration failures.
- PortfolioDemoAgency: identify the synthetic tenant.
- PortfolioDemoPersona: describe a synthetic login persona.
- PortfolioDemoTransaction:
- PortfolioDemoScenario: describe one authored investigation scenario.
- PortfolioDemoWorkflow: describe configured review actors and notes.
- PortfolioDemoExpectation:
- PortfolioDemoProbe:
- PortfolioDemoModel:
- PortfolioDemoAuth:
- PortfolioDemoExecution:

Key functions:
- (none)

Notes:
- Models are frozen and reject unknown fields.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fraudlens_backend.db.models.enums import AlertStatus, SarStatus, UserRole
from fraudlens_backend.settings_defaults import LlmMode, RagEmbeddingMode
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RiskBand

PortfolioDemoAccent = Literal["green", "cyan", "amber", "slate"]
_KNOWN_RULE_CODES: frozenset[str] = frozenset(
    definition.code for definition in DEFAULT_RULE_DEFINITIONS
)


class PortfolioDemoConfigError(RuntimeError):
    """Raised when the portfolio demo config path is unusable or its document is invalid."""


class PortfolioDemoAgency(BaseModel):
    """The single persistent runtime demo tenant, plus the offline study partition it mirrors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID = Field(..., description="Fixed tenant id every demo record is scoped by.")
    name: str = Field(..., min_length=1, description="Synthetic display name (never real).")
    slug: str = Field(..., min_length=1, description="Stable unique slug for the agency row.")
    research_partition_key: str = Field(
        ...,
        min_length=1,
        description=(
            "Name of the OFFLINE research partition this tenant corresponds to in the committed "
            "GFP study artifact; a study-owned analysis concept, never a second runtime tenant."
        ),
    )


class PortfolioDemoPersona(BaseModel):
    """One demo login identity: its seeded user row plus how the login picker presents it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(..., min_length=1, description="Stable config key other sections refer to.")
    seed_user_id: uuid.UUID = Field(..., description="Fixed synthetic `users.id` for this persona.")
    email: str = Field(..., min_length=1, description="Synthetic login email (no real identity).")
    display_name: str = Field(..., min_length=1, description="Human-readable demo user name.")
    initials: str = Field(..., min_length=1, description="Shell avatar initials for this persona.")
    role: UserRole = Field(..., description="RBAC role granted to the persona.")
    picker_name: str = Field(..., min_length=1, description="Label shown in the login picker.")
    picker_tag: str = Field(..., min_length=1, description="Short picker tag (e.g. 'Queue').")
    picker_accent: PortfolioDemoAccent = Field(
        ..., description="Semantic accent token colouring the picker dot (code owns the set)."
    )


class PortfolioDemoTransaction(BaseModel):
    """One authored payload, typed to what `build_canonical` accepts at the ingest boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal = Field(..., gt=Decimal(0), description="Transaction amount (positive).")
    currency: str = Field(..., min_length=1, description="ISO currency code of the amount.")
    origin_account: str = Field(
        ..., min_length=1, description="Obviously-synthetic originating account identifier."
    )
    dest_account: str = Field(
        ..., min_length=1, description="Obviously-synthetic destination account identifier."
    )
    channel: str = Field(..., min_length=1, description="Payment channel (e.g. wire, card, ach).")
    country: str = Field(..., min_length=1, description="Counterparty country code.")
    features: dict[str, Any] = Field(
        default_factory=dict, description="Extra PHI-free feature values carried into ingest."
    )
    occurred_offset_hours: float = Field(
        ...,
        description=(
            "Hours relative to the story anchor (negative = earlier). Offsets, never wall-clock "
            "literals, so repeated runs place the story identically."
        ),
    )


class PortfolioDemoScenario(BaseModel):
    """One story row: its payload, whether the pipeline scores it, and what that must produce."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str = Field(..., min_length=1, description="Unique human-readable story key.")
    external_id_suffix: str = Field(
        ..., min_length=1, description="Suffix the derived transaction external id ends with."
    )
    transaction: PortfolioDemoTransaction = Field(..., description="The authored payload.")
    score: bool = Field(
        ...,
        description="False leaves the row unscored so a visitor can investigate it live.",
    )
    expected_risk_band: RiskBand | None = Field(
        default=None, description="Band real scoring must produce; None for a held-unscored row."
    )
    expected_triggered_rules: tuple[str, ...] = Field(
        default=(),
        description="Rule CODES the row must fire (params live in `aml_rules`, never here).",
    )
    alert_target: AlertStatus | None = Field(
        default=None, description="Alert state the bootstrap transitions this row's alert into."
    )
    sar_target: SarStatus | None = Field(
        default=None, description="SAR state the bootstrap transitions this row's draft into."
    )

    @model_validator(mode="after")
    def _expectations_match_scoring(self) -> PortfolioDemoScenario:
        """A scored row must pin a band; an unscored row must pin no downstream outcome."""
        if self.score and self.expected_risk_band is None:
            raise ValueError(f"scenario '{self.scenario_id}': a scored row needs expected band")
        if not self.score and (
            self.expected_risk_band is not None
            or self.expected_triggered_rules
            or self.alert_target is not None
            or self.sar_target is not None
        ):
            raise ValueError(
                f"scenario '{self.scenario_id}': an unscored row must pin no scored outcome"
            )
        if (self.alert_target is None) != (self.sar_target is None):
            raise ValueError(
                f"scenario '{self.scenario_id}': every alert produces exactly one SAR draft, so "
                "alert_target and sar_target must be set together"
            )
        return self

    @field_validator("expected_triggered_rules")
    @classmethod
    def _known_rule_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject rule codes the baseline rule set does not define (a typo must not pass)."""
        unknown = sorted(set(value) - _KNOWN_RULE_CODES)
        if unknown:
            raise ValueError(f"unknown rule codes: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("expected_triggered_rules contains duplicates")
        return value


class PortfolioDemoWorkflow(BaseModel):
    """Persona KEYS that act on the story's alerts/SARs, plus the synthetic notes they record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_actor: str = Field(..., min_length=1, description="Persona key that assigns.")
    resolution_actor: str = Field(..., min_length=1, description="Persona key that resolves.")
    sar_review_actor: str = Field(..., min_length=1, description="Persona key that reviews SARs.")
    assignee: str = Field(..., min_length=1, description="Persona key an assignment targets.")
    resolution_note: str = Field(..., min_length=1, description="Synthetic note on resolution.")
    approval_note: str = Field(..., min_length=1, description="Synthetic note on SAR approval.")
    rejection_note: str = Field(..., min_length=1, description="Synthetic note on SAR rejection.")


class PortfolioDemoExpectation(BaseModel):
    """The pinned distribution a real pipeline run must reproduce (asserted, never written)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transactions: int = Field(..., ge=0, description="Total transactions the story ingests.")
    unscored: int = Field(..., ge=0, description="Rows deliberately left for a live investigation.")
    risk_bands: dict[RiskBand, int] = Field(
        ..., description="Per-band counts across the SCORED rows (unscored is `risk_band IS NULL`)."
    )
    alert_states: dict[AlertStatus, int] = Field(
        ..., description="Per-status alert counts after the configured transitions."
    )
    sar_states: dict[SarStatus, int] = Field(
        ..., description="Per-status SAR-draft counts after the configured review decisions."
    )

    @field_validator("risk_bands", "alert_states", "sar_states")
    @classmethod
    def _non_negative(cls, value: dict[Any, int]) -> dict[Any, int]:
        """Reject negative counts in any distribution map."""
        if any(count < 0 for count in value.values()):
            raise ValueError("distribution counts must not be negative")
        return value


class PortfolioDemoProbe(BaseModel):
    """Acceptance windows for `--probe` so the calibration report holds no numeric literals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    low_confidence_margin: float = Field(
        ...,
        gt=0.0,
        le=0.5,
        description=(
            "Half-width around the 0.5 decision boundary inside which a probability trips "
            "`low_model_confidence`. Cross-checked at load against "
            "`AppSettings.review_low_confidence_margin` so the two can never drift apart."
        ),
    )
    report_top_n: int = Field(
        ..., gt=0, description="How many contributing features/rules the probe prints per row."
    )


class PortfolioDemoModel(BaseModel):
    """The pinned model and whether its label is intentionally shared with offline research."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_label: str = Field(..., min_length=1, description="Active model version label.")
    shared_with_research: bool = Field(
        ...,
        description=(
            "Whether offline research artifacts may repeat this model label as provenance."
        ),
    )
    feature_spec_version: int = Field(
        ..., gt=0, description="Feature-spec version the pinned bundle must carry."
    )


class PortfolioDemoAuth(BaseModel):
    """Where the public synthetic demo credential comes from (a pointer, never the value)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    public_synthetic_password_env: str = Field(
        ...,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description=(
            "Environment variable supplying the public synthetic demo password. The value lives "
            "in env/Infisical, never inline: `check_no_secrets.py` sanctions exactly this "
            "`*_env` reference form, and `AppSettings.demo_auth_password` reads it."
        ),
    )


class PortfolioDemoExecution(BaseModel):
    """The deterministic provider modes the story's expected outcomes assume."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm_mode: LlmMode = Field(..., description="SAR drafter mode the story is calibrated for.")
    rag_embedding_mode: RagEmbeddingMode = Field(
        ..., description="RAG embedder mode the story is calibrated for."
    )
    multi_agent_sar_enabled: bool = Field(
        ..., description="Tenant runtime feature flag seeded for the demo agency."
    )
    mock_agent_revision_scenario: str = Field(
        ...,
        min_length=1,
        description="Scenario id whose keyless mock agent team performs exactly one revision.",
    )
