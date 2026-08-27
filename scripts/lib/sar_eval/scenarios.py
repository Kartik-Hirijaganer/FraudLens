"""Summary: Deterministic synthetic scenario generation for the SAR evaluation.
Every one of eight typed AML typologies is crossed with the four canonical evidence
variants, producing exactly 32 unique paired scenarios and API-ready transactions.

Key classes:
- SyntheticTransaction: one non-PHI transaction accepted by the shipped ingest API.
- SarEvalScenario: a subject transaction, its history, and expected citation set.
- ScenarioArtifact: the complete deterministic scenario-stage artifact.

Key functions:
- canonical_run_id: derive the only valid run id for a config hash and seed.
- generate_scenarios: build the exact 8 x 4 scenario matrix.
- write_scenarios: serialize the matrix atomically into one run directory.
- load_scenarios: strictly parse a completed scenario artifact.
- validate_scenario_binding: enforce run/config/seed lineage for later stages.

Notes:
- Account labels are conspicuously synthetic and never published in the frontend projection.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from lib.sar_eval.config import (
    SarEvalConfig,
    SarTypology,
    ScenarioVariant,
    validate_config_binding,
)

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
_SAR_CITATION = "31 U.S.C. 5318(g)"
_CITATIONS: dict[SarTypology, tuple[str, ...]] = {
    SarTypology.STRUCTURING: ("31 CFR 1010.314", _SAR_CITATION),
    SarTypology.HIGH_RISK_WIRE: ("31 CFR 1010.610", _SAR_CITATION),
    SarTypology.RAPID_MOVEMENT: ("31 CFR 1010.410", _SAR_CITATION),
    SarTypology.FUNNEL_ACCOUNT: ("31 CFR 1010.410", _SAR_CITATION),
    SarTypology.MULE_VELOCITY: ("31 CFR 1010.410", _SAR_CITATION),
    SarTypology.ROUND_AMOUNT_LAYERING: ("31 CFR 1010.311", _SAR_CITATION),
    SarTypology.CRYPTO_OFF_RAMP: ("31 CFR 1010.410", _SAR_CITATION),
    SarTypology.SHELL_COMPANY_TRANSFER: ("31 CFR 1010.610", _SAR_CITATION),
}
_HISTORY_AMOUNTS: dict[SarTypology, tuple[Decimal, ...]] = {
    SarTypology.STRUCTURING: tuple(
        Decimal(value) for value in ("9200", "9400", "8800", "9600", "9100", "8900")
    ),
    SarTypology.HIGH_RISK_WIRE: tuple(
        Decimal(value) for value in ("15000", "22000", "18000", "31000", "27000", "44000")
    ),
    SarTypology.RAPID_MOVEMENT: tuple(
        Decimal(value) for value in ("15000", "14800", "22000", "21900", "18000", "17750")
    ),
    SarTypology.FUNNEL_ACCOUNT: tuple(
        Decimal(value) for value in ("2400", "3150", "1800", "4250", "2750", "3600")
    ),
    SarTypology.MULE_VELOCITY: tuple(
        Decimal(value) for value in ("475", "470", "825", "810", "390", "385")
    ),
    SarTypology.ROUND_AMOUNT_LAYERING: tuple(
        Decimal(value) for value in ("10000", "25000", "15000", "30000", "20000", "40000")
    ),
    SarTypology.CRYPTO_OFF_RAMP: tuple(
        Decimal(value) for value in ("3200", "5100", "2750", "8400", "4600", "6900")
    ),
    SarTypology.SHELL_COMPANY_TRANSFER: tuple(
        Decimal(value) for value in ("75000", "120000", "98000", "150000", "110000", "175000")
    ),
}
_HISTORY_DIRECTIONS: dict[SarTypology, tuple[str, ...]] = {
    SarTypology.STRUCTURING: ("in",) * 6,
    SarTypology.HIGH_RISK_WIRE: ("out",) * 6,
    SarTypology.RAPID_MOVEMENT: ("in", "out") * 3,
    SarTypology.FUNNEL_ACCOUNT: ("in",) * 6,
    SarTypology.MULE_VELOCITY: ("in", "out") * 3,
    SarTypology.ROUND_AMOUNT_LAYERING: ("out",) * 6,
    SarTypology.CRYPTO_OFF_RAMP: ("in", "in", "out", "in", "out", "in"),
    SarTypology.SHELL_COMPANY_TRANSFER: ("in", "out") * 3,
}
_PATTERN_CHANNEL: dict[SarTypology, str] = {
    SarTypology.STRUCTURING: "cash_deposit",
    SarTypology.HIGH_RISK_WIRE: "international_wire",
    SarTypology.RAPID_MOVEMENT: "same_day_wire",
    SarTypology.FUNNEL_ACCOUNT: "branch_cash",
    SarTypology.MULE_VELOCITY: "peer_to_peer",
    SarTypology.ROUND_AMOUNT_LAYERING: "corporate_wire",
    SarTypology.CRYPTO_OFF_RAMP: "crypto_exchange",
    SarTypology.SHELL_COMPANY_TRANSFER: "business_wire",
}
_PATTERN_COUNTRY: dict[SarTypology, str] = {
    SarTypology.STRUCTURING: "US",
    SarTypology.HIGH_RISK_WIRE: "IR",
    SarTypology.RAPID_MOVEMENT: "US",
    SarTypology.FUNNEL_ACCOUNT: "US",
    SarTypology.MULE_VELOCITY: "US",
    SarTypology.ROUND_AMOUNT_LAYERING: "KY",
    SarTypology.CRYPTO_OFF_RAMP: "SV",
    SarTypology.SHELL_COMPANY_TRANSFER: "PA",
}
_SPACING_MINUTES: dict[SarTypology, int] = {
    SarTypology.STRUCTURING: 24 * 60,
    SarTypology.HIGH_RISK_WIRE: 12 * 60,
    SarTypology.RAPID_MOVEMENT: 10,
    SarTypology.FUNNEL_ACCOUNT: 48 * 60,
    SarTypology.MULE_VELOCITY: 5,
    SarTypology.ROUND_AMOUNT_LAYERING: 24 * 60,
    SarTypology.CRYPTO_OFF_RAMP: 6 * 60,
    SarTypology.SHELL_COMPANY_TRANSFER: 72 * 60,
}
_SUBJECT_PATTERN: dict[SarTypology, tuple[Decimal, str, str, str]] = {
    SarTypology.STRUCTURING: (Decimal("9700"), "in", "cash_deposit", "US"),
    SarTypology.HIGH_RISK_WIRE: (Decimal("48000"), "out", "international_wire", "IR"),
    SarTypology.RAPID_MOVEMENT: (Decimal("14900"), "out", "same_day_wire", "US"),
    SarTypology.FUNNEL_ACCOUNT: (Decimal("17500"), "out", "outbound_wire", "US"),
    SarTypology.MULE_VELOCITY: (Decimal("780"), "out", "peer_to_peer", "US"),
    SarTypology.ROUND_AMOUNT_LAYERING: (Decimal("50000"), "out", "corporate_wire", "KY"),
    SarTypology.CRYPTO_OFF_RAMP: (Decimal("13800"), "in", "crypto_exchange", "SV"),
    SarTypology.SHELL_COMPANY_TRANSFER: (Decimal("125000"), "out", "business_wire", "PA"),
}


class SyntheticTransaction(BaseModel):
    """One API-ingestible synthetic transaction with no real person or account data."""

    model_config = _MODEL_CONFIG

    external_id: str = Field(..., min_length=1, description="Unique synthetic transaction key.")
    amount: Decimal = Field(..., gt=0, description="Synthetic transaction amount.")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code.")
    occurred_at: datetime = Field(..., description="Deterministic UTC event time.")
    origin_account: str = Field(..., min_length=1, description="Synthetic origin label.")
    dest_account: str = Field(..., min_length=1, description="Synthetic destination label.")
    channel: str = Field(..., min_length=1, description="Synthetic origination channel.")
    country: str = Field(..., min_length=2, max_length=2, description="ISO country code.")
    features: dict[str, object] = Field(
        default_factory=dict, description="Synthetic model features."
    )


class SarEvalScenario(BaseModel):
    """One paired scenario: history followed by the transaction both arms investigate."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Stable typology-variant key.")
    typology: SarTypology = Field(..., description="Synthetic AML pattern.")
    variant: ScenarioVariant = Field(..., description="Evidence-quality variant.")
    subject_external_id: str = Field(..., min_length=1, description="Transaction investigated.")
    transactions: tuple[SyntheticTransaction, ...] = Field(
        ..., min_length=1, description="Chronological history ending in the subject."
    )
    expected_citation_ids: tuple[str, ...] = Field(
        ..., min_length=1, description="Scenario-relevant closed-vocabulary citations."
    )

    @model_validator(mode="after")
    def _subject_is_last(self) -> SarEvalScenario:
        ids = [item.external_id for item in self.transactions]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario transaction external ids must be unique")
        if ids[-1] != self.subject_external_id:
            raise ValueError("scenario subject must be the final transaction")
        return self


class ScenarioArtifact(BaseModel):
    """The complete deterministic scenario-stage artifact."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Protocol-derived evaluation run id.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Config byte hash.")
    seed: int = Field(..., ge=0, description="Scenario seed.")
    scenarios: tuple[SarEvalScenario, ...] = Field(
        ..., min_length=32, max_length=32, description="Exact 8 x 4 scenario matrix."
    )

    @model_validator(mode="after")
    def _complete_matrix(self) -> ScenarioArtifact:
        keys = {(item.typology, item.variant) for item in self.scenarios}
        expected = {(typology, variant) for typology in SarTypology for variant in ScenarioVariant}
        if keys != expected or len(self.scenarios) != len(expected):
            raise ValueError("scenario artifact must contain every typology x variant exactly once")
        if len({item.scenario_id for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("scenario ids must be unique")
        return self


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _anchor(config: SarEvalConfig) -> datetime:
    parsed = config.anchor_time
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _transaction(  # noqa: PLR0913 -- mirrors the transaction API boundary.
    run_id: str,
    scenario_id: str,
    position: int,
    occurred_at: datetime,
    *,
    amount: Decimal,
    direction: str,
    channel: str,
    country: str,
) -> SyntheticTransaction:
    hub = f"SYNTH-HUB-{run_id}-{scenario_id}"
    counterparty = f"SYNTH-CP-{run_id}-{scenario_id}-{position:02d}"
    origin, destination = (counterparty, hub) if direction == "in" else (hub, counterparty)
    return SyntheticTransaction(
        external_id=f"{run_id}-{scenario_id}-{position:02d}",
        amount=amount,
        currency="USD",
        occurred_at=occurred_at,
        origin_account=origin,
        dest_account=destination,
        channel=channel,
        country=country,
        features={"syntheticStudy": 1, "sequencePosition": position},
    )


def _scenario(
    config: SarEvalConfig,
    run_id: str,
    typology: SarTypology,
    variant: ScenarioVariant,
) -> SarEvalScenario:
    scenario_id = f"{typology.value}-{variant.value}"
    index = list(SarTypology).index(typology)
    base_time = _anchor(config) + timedelta(days=index * 3)
    history_count = 2 if variant is ScenarioVariant.THIN_EVIDENCE else 6
    history: list[SyntheticTransaction] = []
    for position in range(history_count):
        history.append(
            _transaction(
                run_id,
                scenario_id,
                position,
                base_time + timedelta(minutes=position * _SPACING_MINUTES[typology]),
                amount=_HISTORY_AMOUNTS[typology][position],
                direction=_HISTORY_DIRECTIONS[typology][position],
                channel=_PATTERN_CHANNEL[typology],
                country=_PATTERN_COUNTRY[typology],
            )
        )
    subject_amount, subject_direction, channel, subject_country = _SUBJECT_PATTERN[typology]
    if variant is ScenarioVariant.CONFLICTING_EVIDENCE:
        channel = "payroll"
    if variant is ScenarioVariant.CITATION_BAIT:
        channel = f"{channel} reference 31 CFR 9999.999"
    subject = _transaction(
        run_id,
        scenario_id,
        history_count,
        base_time + timedelta(minutes=history_count * _SPACING_MINUTES[typology]),
        amount=subject_amount,
        direction=subject_direction,
        channel=channel,
        country=subject_country,
    )
    return SarEvalScenario(
        scenario_id=scenario_id,
        typology=typology,
        variant=variant,
        subject_external_id=subject.external_id,
        transactions=(*history, subject),
        expected_citation_ids=_CITATIONS[typology],
    )


def canonical_run_id(config_sha256: str, seed: int) -> str:
    """Derive the only valid evaluation run id for a protocol hash and seed."""
    payload = json.dumps({"configSha256": config_sha256, "seed": seed}, sort_keys=True)
    return f"sar-eval-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def generate_scenarios(config: SarEvalConfig, config_bytes: bytes) -> ScenarioArtifact:
    """Build the canonical scenario matrix and its deterministic protocol id."""
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    validate_config_binding(config, config_hash)
    run_id = canonical_run_id(config_hash, config.seed)
    scenarios = tuple(
        _scenario(config, run_id, typology, variant)
        for typology in config.typologies
        for variant in config.variants
    )
    return ScenarioArtifact(
        run_id=run_id,
        config_sha256=config_hash,
        seed=config.seed,
        scenarios=scenarios,
    )


def write_scenarios(path: Path, artifact: ScenarioArtifact) -> None:
    """Atomically serialize a scenario artifact with stable camelCase JSON."""
    if path.parent.name != artifact.run_id:
        raise ValueError("scenario artifact path must be nested under its exact run id")
    content = (
        json.dumps(artifact.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True) + "\n"
    )
    _atomic_write(path, content)


def load_scenarios(path: Path) -> ScenarioArtifact:
    """Load and strictly validate one scenario artifact."""
    artifact = ScenarioArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    validate_scenario_binding(artifact, expected_run_id=path.parent.name)
    return artifact


def validate_scenario_binding(
    artifact: ScenarioArtifact,
    *,
    expected_run_id: str,
    config: SarEvalConfig | None = None,
) -> None:
    """Require path/CLI run identity and, when supplied, exact protocol SHA and seed lineage."""
    if artifact.run_id != expected_run_id:
        raise ValueError("scenario artifact run id does not match the requested CLI run id")
    if artifact.run_id != canonical_run_id(artifact.config_sha256, artifact.seed):
        raise ValueError("scenario artifact run id is not canonical for its config SHA and seed")
    if config is not None:
        validate_config_binding(config, artifact.config_sha256)
        if artifact.seed != config.seed:
            raise ValueError("scenario artifact seed does not match the loaded protocol")
