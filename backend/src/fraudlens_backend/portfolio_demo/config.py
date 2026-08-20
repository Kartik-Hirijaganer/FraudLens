"""Summary: The single validated source of the portfolio demo story (`config/portfolio-demo.yaml`).
Every demo-specific value — the one runtime agency, its personas, the pinned model label, the
authored case-pack scenarios, the expected distribution, and the workflow actors/notes — is
declared in that YAML and reaches the seed, the dev bypass, the provisioning script, the batch
runner, and the bootstrap through `load_portfolio_demo_config()`; no consumer holds a demo
identity constant (rule 4). The models are frozen with `extra="forbid"`, so an unknown or renamed
key fails before any write, and the cross-field validators assert the story's ALGEBRA (counts
agree with the scenario list, actors resolve to personas whose roles carry the required
permission) rather than restating expected numbers in code.

Key classes:
- PortfolioDemoConfigError: raised for an unusable path or an invalid story document.
- PortfolioDemoAgency: the single runtime demo tenant plus its offline research partition key.
- PortfolioDemoPersona: one demo login identity (seed id, email, role, picker presentation).
- PortfolioDemoTransaction: one authored payload, anchored by offset instead of wall-clock time.
- PortfolioDemoScenario: one story row - its payload, whether it is scored, and its expectations.
- PortfolioDemoWorkflow: the persona keys and synthetic notes used for alert/SAR transitions.
- PortfolioDemoExpectation: the pinned transaction/band/alert/SAR distribution.
- PortfolioDemoProbe: calibration-report acceptance windows (cross-checked against settings).
- PortfolioDemoModel: the pinned model version label and feature-spec version.
- PortfolioDemoAuth: the env-var pointer supplying the public synthetic demo password.
- PortfolioDemoExecution: the deterministic LLM/RAG provider modes the story is calibrated for.
- PortfolioDemoConfig: the whole validated story document (root model).

Key functions:
- load_portfolio_demo_config: resolve, parse, and validate the story config (process-cached).
- clear_portfolio_demo_config_cache: drop the process cache so tests can reload a fresh document.

Notes:
- Path safety: the settings-supplied value is a FILENAME under `find_config_dir()`. Absolute
  paths, `~`, upward traversal, and symlinks escaping the config dir are rejected. An explicit
  `path` (tests, and the bootstrap's `--config` override) is operator-supplied and only has to
  exist, mirroring `load_gfp_benchmark_config`.
- Validation failures report only the field LOCATION and error type, never the offending value,
  so an error can never echo an authored account, amount, or credential.
- The validated document is cached PER PROCESS, keyed by resolved path + probe window. A YAML
  edit is therefore NOT picked up by an already-running server or CLI: restart it (the operator
  action), or call `clear_portfolio_demo_config_cache()` from a test. This is deliberate — the
  story must not change underneath an in-flight bootstrap, verification, or request.
- The synthetic demo password is NOT here: `check_no_secrets.py` rejects an inline value for a
  `password` key, so the YAML carries `public_synthetic_password_env` (the scanner's sanctioned
  env-reference escape) and the value resolves from env/Infisical through `AppSettings`.
- Alerting bands are derived from `RiskPolicy` (a band alerts when its lower bound reaches the
  alert threshold), so the "only high/critical may carry alert targets" rule is never a literal.
- `AUDIT_ACTION`, the advisory-lock key, the audit request id, and a persona's history-only seed
  address all derive from the story identity (`external_id_namespace` + `story_version`, or the
  agency slug), so nothing restates them and a story bump moves them together.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from fraudlens_backend.db.models.enums import AlertStatus, SarStatus, UserRole
from fraudlens_backend.settings import (
    AppSettings,
    LlmMode,
    RagEmbeddingMode,
    find_config_dir,
    get_settings,
)
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RiskBand, RiskPolicy
from fraudlens_core.phi import mask_identifier

# The audited action every portfolio-demo write is recorded under (one constant, never a literal
# repeated at each call site); the request id derives from it plus the story identity.
AUDIT_ACTION = "portfolio_demo.bootstrap"

# The semantic accent tokens the login picker may paint a persona with. CODE owns the allowed
# set (so `DESIGN.md`'s palette rules cannot be violated from YAML); config only selects one.
PortfolioDemoAccent = Literal["green", "cyan", "amber", "slate"]

_KNOWN_RULE_CODES: frozenset[str] = frozenset(
    definition.code for definition in DEFAULT_RULE_DEFINITIONS
)

# A Postgres advisory lock key is a signed 64-bit integer.
_LOCK_KEY_BYTES = 8
_LOCK_KEY_SIGNED = True


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
    """The pinned scoring model the story's expected distribution was calibrated against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_label: str = Field(..., min_length=1, description="Active model version label.")
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


def _safe_reason(error: ValidationError) -> str:
    """Summarize a validation failure by field LOCATION and error type only (never the value)."""
    parts: list[str] = []
    for detail in error.errors():
        location = ".".join(str(item) for item in detail["loc"]) or "<root>"
        # `value_error` messages come from the validators above, which are PHI-free by
        # construction; every other type reports its code alone so no input can leak.
        if detail["type"] == "value_error":
            parts.append(f"{location}: {detail['msg']}")
        else:
            parts.append(f"{location}: {detail['type']}")
    return "; ".join(parts)


def _resolve_config_path(config_dir: Path, filename: str) -> Path:
    """Resolve a settings-supplied FILENAME inside the config dir, rejecting any escape."""
    candidate = Path(filename)
    if not filename or filename.startswith("~") or candidate.is_absolute():
        raise PortfolioDemoConfigError(
            "portfolio demo config must be a relative filename under the config directory"
        )
    if ".." in candidate.parts:
        raise PortfolioDemoConfigError("portfolio demo config must not traverse upward")
    base = config_dir.resolve()
    resolved = (base / candidate).resolve()  # follows symlinks, so an escaping link is caught
    if not resolved.is_relative_to(base):
        raise PortfolioDemoConfigError(
            "portfolio demo config resolves outside the config directory"
        )
    if not resolved.is_file():
        raise PortfolioDemoConfigError(f"portfolio demo config '{filename}' is missing")
    return resolved


@lru_cache(maxsize=8)
def _load_validated(target: Path, low_confidence_margin: float) -> PortfolioDemoConfig:
    """Parse + validate one story document, cross-checking the probe against settings."""
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PortfolioDemoConfigError("portfolio demo config could not be read as YAML") from exc
    if not isinstance(raw, dict):
        raise PortfolioDemoConfigError("portfolio demo config must contain a YAML mapping")
    try:
        config = PortfolioDemoConfig.model_validate(raw)
    except ValidationError as exc:
        raise PortfolioDemoConfigError(
            f"portfolio demo config is invalid — {_safe_reason(exc)}"
        ) from exc
    if config.probe.low_confidence_margin != low_confidence_margin:
        raise PortfolioDemoConfigError(
            "probe.low_confidence_margin does not match review_low_confidence_margin"
        )
    return config


def load_portfolio_demo_config(
    path: Path | None = None, *, settings: AppSettings | None = None
) -> PortfolioDemoConfig:
    """Return the validated portfolio demo story (process-cached per path + probe window).

    With no `path`, the location is `AppSettings.portfolio_demo_config_file` resolved under
    `find_config_dir()` with full containment validation. An explicit `path` is operator-supplied
    (tests and the bootstrap's `--config` override) and only has to exist.
    """
    resolved_settings = settings or get_settings()
    if path is None:
        target = _resolve_config_path(
            find_config_dir(), resolved_settings.portfolio_demo_config_file
        )
    elif not path.is_file():
        raise PortfolioDemoConfigError("portfolio demo config path is not a file")
    else:
        target = path.resolve()
    return _load_validated(target, resolved_settings.review_low_confidence_margin)


def clear_portfolio_demo_config_cache() -> None:
    """Drop the per-process cache so the document is parsed again.

    The cache is deliberately not invalidated by an on-disk edit: a running server or CLI keeps
    the story it started with, so nothing changes underneath an in-flight bootstrap or request.
    Restart the process to pick up an edit; tests call this instead.
    """
    _load_validated.cache_clear()
