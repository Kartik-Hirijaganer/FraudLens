"""Summary: The versioned cascade scenario contract (release 0.5.0 Phase 4.3/4.5/4.8). A v1 "arm"
was one pinned model behind one endpoint; a v2 SCENARIO is one production SAR profile measured over
the named endpoint roles it needs, so the benchmark measures the shipped
`QualityGatedSarDrafter` rather than a benchmark-only client. A scenario therefore owns no model
reference and no decoding mode of its own: it names a profile in the production SAR routing file
and the endpoint roles that profile's connections resolve to, which keeps exactly one source for
what the product actually runs. Endpoint roles bind to the frozen `arms` pins, so two
simultaneously-provisioned endpoints stay bound to the same revisions, image digest, and price
provenance the raw comparison uses.

Key classes:
- EndpointRoleConfig: one named endpoint role bound to a frozen arm and a production connection.
- ScenarioConfig: one measured scenario referencing a production SAR profile.
- CascadeReportConfig: which scenario is the baseline and what the report discloses.
- CascadeConfig: the complete v2 cascade matrix, replay-pilot binding, and spend provenance.

Key functions:
- (none)

Notes:
- Concurrency levels are per scenario because the cascade scenarios need two paid endpoints at
  once: the raw quantization comparison keeps all three levels on ONE endpoint (equal hardware),
  while cascade scenarios run the level the replay pilot sized and costed. A scenario may not
  declare a level outside `load.concurrency_levels`, so no scenario can silently widen the matrix.
- Multi-endpoint scenarios are the architecture comparison, never a same-resource claim; the
  report carries GPU-hours per case and aggregate resident memory so that stays visible.
- A role's GPU sampler is addressed through an ENV VAR NAME, never a host or key in config, so two
  simultaneously provisioned endpoints are sampled independently without committing an address.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class EndpointRoleConfig(BaseModel):
    """One named serving role: a frozen arm reached over a production named connection."""

    model_config = _MODEL_CONFIG

    arm: str = Field(..., min_length=1, description="Frozen arm whose pins this role serves.")
    connection: str = Field(
        ..., min_length=1, description="Named connection in the production provider registry."
    )
    telemetry_prefix_env: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]+$",
        description="Env var naming this role's GPU-sampler command prefix (no host in config).",
    )


class ScenarioConfig(BaseModel):
    """One measured scenario: a production SAR profile over its required endpoint roles."""

    model_config = _MODEL_CONFIG

    name: str = Field(..., min_length=1, description="Stable scenario identity in checkpoints.")
    profile: str = Field(..., min_length=1, description="Production SAR routing profile id.")
    endpoints: tuple[str, ...] = Field(
        ..., min_length=1, description="Endpoint roles that must be reachable for this scenario."
    )
    concurrency_levels: tuple[int, ...] = Field(
        ..., min_length=1, description="Closed-loop levels this scenario is measured at."
    )

    @field_validator("endpoints", "concurrency_levels")
    @classmethod
    def _unique(cls, values: tuple[str | int, ...]) -> tuple[str | int, ...]:
        if len(set(values)) != len(values):
            raise ValueError("scenario endpoints and concurrency levels must be unique")
        return values


class CascadeReportConfig(BaseModel):
    """What the published cascade report compares against, and what it admits about itself."""

    model_config = _MODEL_CONFIG

    baseline: str = Field(
        ..., min_length=1, description="Scenario every published comparison is measured against."
    )
    disclosures: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description="Limitations published WITH the evidence, declared here rather than authored "
        "into the renderer so what the report admits stays reviewable next to the protocol.",
    )


class CascadeConfig(BaseModel):
    """The frozen cascade scenario matrix, replay-pilot binding, and spend provenance."""

    model_config = _MODEL_CONFIG

    sar_config_file: str = Field(
        ..., min_length=1, description="Production SAR routing file the profiles are read from."
    )
    replay_run_id: str = Field(
        ..., min_length=1, description="Persisted run the free replay pilot is composed from."
    )
    replay_policy: str = Field(
        ..., min_length=1, description="Named config/quality.yaml policy the replay is judged by."
    )
    allocation: str = Field(
        ..., min_length=1, description="Experiment budget allocation the live matrix draws on."
    )
    rate_key: str = Field(
        ..., min_length=1, description="Budget rate quote priced per provisioned endpoint hour."
    )
    endpoints: dict[str, EndpointRoleConfig] = Field(
        ..., min_length=1, description="Named endpoint roles the scenarios may require."
    )
    scenarios: tuple[ScenarioConfig, ...] = Field(
        ..., min_length=1, description="Ordered measured scenarios."
    )
    report: CascadeReportConfig = Field(
        ..., description="Published-report baseline and disclosures."
    )

    @model_validator(mode="after")
    def _resolvable_matrix(self) -> CascadeConfig:
        names = [scenario.name for scenario in self.scenarios]
        if len(set(names)) != len(names):
            raise ValueError("cascade scenario names must be unique")
        profiles = [scenario.profile for scenario in self.scenarios]
        if len(set(profiles)) != len(profiles):
            raise ValueError("each cascade scenario must measure a distinct SAR profile")
        unknown = {
            role
            for scenario in self.scenarios
            for role in scenario.endpoints
            if role not in self.endpoints
        }
        if unknown:
            raise ValueError(
                f"cascade scenarios reference unknown endpoint roles: {sorted(unknown)}"
            )
        if self.report.baseline not in names:
            raise ValueError("the report baseline must name a declared scenario")
        return self

    def scenario(self, name: str) -> ScenarioConfig:
        """Return one declared scenario or fail closed on an unknown name."""
        for scenario in self.scenarios:
            if scenario.name == name:
                return scenario
        raise ValueError(f"unknown benchmark scenario '{name}'")

    def endpoint_count(self, name: str) -> int:
        """Return how many paid endpoints one scenario keeps provisioned at once."""
        return len(self.scenario(name).endpoints)
