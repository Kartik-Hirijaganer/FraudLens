"""Summary: Root portfolio-demo story model, derived identities, and cross-field validation.

Key classes:
- PortfolioDemoConfig: validate the complete configured demo story.

Key functions:
- (none)

Notes:
- Derived identifiers keep repeated demo runs deterministic without duplicating literals.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fraudlens_backend.db.models.enums import UserRole
from fraudlens_backend.portfolio_demo.config_models import (
    PortfolioDemoAgency,
    PortfolioDemoAuth,
    PortfolioDemoConfigError,
    PortfolioDemoExecution,
    PortfolioDemoExpectation,
    PortfolioDemoModel,
    PortfolioDemoPersona,
    PortfolioDemoProbe,
    PortfolioDemoScenario,
    PortfolioDemoWorkflow,
)
from fraudlens_core import RiskBand, RiskPolicy
from fraudlens_core.phi import mask_identifier

AUDIT_ACTION = "portfolio_demo.bootstrap"
_LOCK_KEY_BYTES = 8
_LOCK_KEY_SIGNED = True


class PortfolioDemoConfig(BaseModel):
    """The whole validated portfolio demo story (`config/portfolio-demo.yaml`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(
        ..., gt=0, description="Document schema version; a breaking shape change bumps it."
    )
    story_version: str = Field(
        ...,
        min_length=1,
        description="Story revision; derived external ids, lock key, and request id all carry it.",
    )
    external_id_namespace: str = Field(
        ..., min_length=1, description="Namespace prefix every derived external id starts with."
    )
    story_anchor: datetime = Field(
        ...,
        description="Timezone-aware instant each `occurred_offset_hours` is measured from.",
    )
    agency: PortfolioDemoAgency = Field(..., description="The single runtime demo tenant.")
    personas: tuple[PortfolioDemoPersona, ...] = Field(
        ..., min_length=1, description="Demo login identities seeded into the agency."
    )
    default_bypass_persona: str = Field(
        ...,
        min_length=1,
        description="Persona key the non-prod dev bypass mints when no role is requested.",
    )
    auth: PortfolioDemoAuth = Field(..., description="Where the synthetic demo password lives.")
    model: PortfolioDemoModel = Field(..., description="The pinned model identity.")
    execution: PortfolioDemoExecution = Field(..., description="Pinned provider execution modes.")
    case_pack_partition_count: int = Field(
        ...,
        gt=0,
        description="Number of tenant partitions the demo case-pack ingest spreads rows across.",
    )
    case_pack_tenant_weights: tuple[int, ...] = Field(
        ...,
        min_length=1,
        description=(
            "Partition-index cycle the case-pack anchor selection walks; the offline GFP study "
            "keeps its own default so this value never moves the published artifact."
        ),
    )
    workflow: PortfolioDemoWorkflow = Field(..., description="Configured actors and review notes.")
    expected: PortfolioDemoExpectation = Field(..., description="The pinned distribution.")
    probe: PortfolioDemoProbe = Field(..., description="Calibration-report acceptance windows.")
    scenarios: tuple[PortfolioDemoScenario, ...] = Field(
        ..., description="The authored case pack, in the order the bootstrap ingests and scores."
    )

    # --- Derived identity (never literals) -------------------------------------------------
    @property
    def story_identity(self) -> str:
        """Return the namespace+version identity every derived id is built from."""
        return f"{self.external_id_namespace}-{self.story_version}"

    @property
    def audit_request_id(self) -> str:
        """Return the PHI-free request id the bootstrap's audit rows are correlated by."""
        return f"{AUDIT_ACTION}:{self.story_identity}"

    @property
    def advisory_lock_key(self) -> int:
        """Return the signed 64-bit Postgres advisory-lock key for this story identity."""
        digest = hashlib.sha256(self.story_identity.encode("utf-8")).digest()
        return int.from_bytes(digest[:_LOCK_KEY_BYTES], "big", signed=_LOCK_KEY_SIGNED)

    def external_id(self, scenario: PortfolioDemoScenario) -> str:
        """Return a scenario's derived transaction external id."""
        return f"{self.story_identity}-{scenario.external_id_suffix}"

    def history_email(self, persona: PortfolioDemoPersona) -> str:
        """Return the non-login address a persona's FIXED SEED actor is addressed by.

        `users.email` is globally unique, so the seeded `seed_user_id` row and the auth-backed row
        provisioning mirrors in cannot both hold the login address. The seed actor takes this
        derived one and stays as the history-only actor that owns `alert_actions.actor_id`,
        `sar_drafts.reviewed_by`, and `training_labels.created_by` — which is why the bootstrap can
        resolve an actor by `seed_user_id` no matter how many times Supabase re-issues an auth id.
        Derived here so the seed, the provisioning script, and the tests cannot disagree (rule 5).
        """
        return f"seed-{persona.key}@{self.agency.slug}.test"

    def occurred_at(self, scenario: PortfolioDemoScenario) -> datetime:
        """Return a scenario's absolute occurrence instant, anchored by the configured moment."""
        return self.story_anchor + timedelta(hours=scenario.transaction.occurred_offset_hours)

    # --- Lookups ---------------------------------------------------------------------------
    def persona(self, key: str) -> PortfolioDemoPersona:
        """Return the persona with `key`; raise when the key is not configured."""
        for persona in self.personas:
            if persona.key == key:
                return persona
        raise PortfolioDemoConfigError(f"portfolio demo persona '{key}' is not configured")

    def persona_for_role(self, role: UserRole) -> PortfolioDemoPersona | None:
        """Return the first persona carrying `role`, or None when no persona has it."""
        return next((persona for persona in self.personas if persona.role is role), None)

    @property
    def scored_scenarios(self) -> tuple[PortfolioDemoScenario, ...]:
        """Return the scenarios the bootstrap passes to the batch scorer, in configured order."""
        return tuple(scenario for scenario in self.scenarios if scenario.score)

    # --- Validators ------------------------------------------------------------------------
    @field_validator("story_anchor")
    @classmethod
    def _anchor_is_aware(cls, value: datetime) -> datetime:
        """Reject a naive anchor so offsets resolve to unambiguous instants."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("story_anchor must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _unique_identities(self) -> PortfolioDemoConfig:
        """Persona keys/ids/emails and scenario ids/external-id suffixes must each be unique."""
        groups: tuple[tuple[str, list[Any]], ...] = (
            ("persona keys", [persona.key for persona in self.personas]),
            ("persona ids", [persona.seed_user_id for persona in self.personas]),
            ("persona emails", [persona.email.lower() for persona in self.personas]),
            ("scenario ids", [scenario.scenario_id for scenario in self.scenarios]),
            (
                "external id suffixes",
                [scenario.external_id_suffix for scenario in self.scenarios],
            ),
        )
        for label, values in groups:
            if len(set(values)) != len(values):
                raise ValueError(f"{label} must be unique")
        return self

    @model_validator(mode="after")
    def _mock_revision_scenario_exists(self) -> PortfolioDemoConfig:
        """Require the designated mock revision scenario to resolve to one authored case."""
        if self.execution.mock_agent_revision_scenario not in {
            scenario.scenario_id for scenario in self.scenarios
        }:
            raise ValueError("execution.mock_agent_revision_scenario must name a scenario")
        return self

    @model_validator(mode="after")
    def _actors_resolve_and_are_permitted(self) -> PortfolioDemoConfig:
        """Every configured persona key resolves and carries the permission its role must have.

        The RBAC policy stays owned by the API boundary (`api/deps.py`); it is imported lazily so
        that module can import this one for the dev bypass without an import cycle.
        """
        from fraudlens_backend.api.deps import Permission, role_has_permission  # noqa: PLC0415

        required: tuple[tuple[str, str, Permission], ...] = (
            ("default_bypass_persona", self.default_bypass_persona, Permission.VIEW),
            ("workflow.assignment_actor", self.workflow.assignment_actor, Permission.TRIAGE_ALERT),
            (
                "workflow.resolution_actor",
                self.workflow.resolution_actor,
                Permission.FINALIZE_ALERT,
            ),
            ("workflow.sar_review_actor", self.workflow.sar_review_actor, Permission.REVIEW_SAR),
            ("workflow.assignee", self.workflow.assignee, Permission.VIEW),
        )
        keys = {persona.key: persona for persona in self.personas}
        for field, key, permission in required:
            persona = keys.get(key)
            if persona is None:
                raise ValueError(f"{field}: '{key}' does not resolve to a configured persona")
            if not role_has_permission(persona.role.value, permission):
                raise ValueError(
                    f"{field}: role '{persona.role.value}' does not grant '{permission.value}'"
                )
        return self

    @model_validator(mode="after")
    def _distribution_algebra(self) -> PortfolioDemoConfig:
        """The pinned counts must be the ALGEBRAIC consequence of the scenario list."""
        scored = [scenario for scenario in self.scenarios if scenario.score]
        checks: tuple[tuple[str, int, int], ...] = (
            ("expected.transactions", self.expected.transactions, len(self.scenarios)),
            ("expected.unscored", self.expected.unscored, len(self.scenarios) - len(scored)),
            ("expected.risk_bands total", sum(self.expected.risk_bands.values()), len(scored)),
            (
                "expected.alert_states total",
                sum(self.expected.alert_states.values()),
                sum(1 for scenario in self.scenarios if scenario.alert_target is not None),
            ),
            (
                "expected.sar_states total",
                sum(self.expected.sar_states.values()),
                sum(1 for scenario in self.scenarios if scenario.sar_target is not None),
            ),
        )
        for label, declared, derived in checks:
            if declared != derived:
                raise ValueError(f"{label} is {declared} but the scenarios imply {derived}")
        for band, count in self.expected.risk_bands.items():
            derived = sum(1 for scenario in scored if scenario.expected_risk_band is band)
            if count != derived:
                raise ValueError(
                    f"expected.risk_bands[{band.value}] is {count}, scenarios imply {derived}"
                )
        return self

    @model_validator(mode="after")
    def _only_alerting_bands_carry_targets(self) -> PortfolioDemoConfig:
        """A row may carry alert/SAR targets only when its band actually crosses the threshold.

        The alerting bands are DERIVED from `RiskPolicy` (a band alerts once its lower bound
        reaches the alert threshold), so this rule never restates which bands those are.
        """
        policy = RiskPolicy()
        alerting = {
            band
            for band in RiskBand
            if policy.band_thresholds.get(band, 0.0) >= policy.alert_threshold
        }
        for scenario in self.scenarios:
            if scenario.alert_target is None:
                continue
            if scenario.expected_risk_band not in alerting:
                raise ValueError(
                    f"scenario '{scenario.scenario_id}': band "
                    f"'{scenario.expected_risk_band}' does not reach the alert threshold, so it "
                    "must not declare alert_target/sar_target"
                )
        return self

    @model_validator(mode="after")
    def _masked_accounts_do_not_collide(self) -> PortfolioDemoConfig:
        """Distinct authored accounts must survive masking distinctly.

        Persisted accounts collapse to their last four characters and same-account history is
        grouped on the MASKED value, so two different authored accounts that mask identically
        would silently share history. The collapse is computed with the real masker rather than
        restated here.
        """
        owners: dict[str, str] = {}
        for scenario in self.scenarios:
            accounts = (scenario.transaction.origin_account, scenario.transaction.dest_account)
            for account in accounts:
                masked = mask_identifier(account).value
                previous = owners.setdefault(masked, account)
                if previous != account:
                    raise ValueError(
                        f"scenario '{scenario.scenario_id}': two distinct accounts mask to the "
                        "same value, which would merge their history windows"
                    )
        return self

    @model_validator(mode="after")
    def _case_pack_partitions_agree(self) -> PortfolioDemoConfig:
        """The tenant cycle must have one entry per partition and index only real partitions."""
        if len(self.case_pack_tenant_weights) != self.case_pack_partition_count:
            raise ValueError(
                f"case_pack_tenant_weights has {len(self.case_pack_tenant_weights)} entries but "
                f"case_pack_partition_count is {self.case_pack_partition_count}"
            )
        out_of_range = [
            weight
            for weight in self.case_pack_tenant_weights
            if not 0 <= weight < self.case_pack_partition_count
        ]
        if out_of_range:
            raise ValueError(
                f"case_pack_tenant_weights entries outside the partitions: {out_of_range}"
            )
        return self
