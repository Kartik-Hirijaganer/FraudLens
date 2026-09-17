"""Summary: The deterministic SAR evidence catalog (release 0.5.0 Phase 2.1). It derives the
closed set of facts a draft may reference — and must match — from the ALREADY-PROJECTED
`SarModelInput`, so it introduces no new data source, no PHI, and no database identifiers: every
value in it has already passed `project_for_model`'s allowlist, alias, and forbidden-byte checks.
Each entry carries a stable synthetic ref id (`txn.amount`, `rule.1.type`, `driver.<feature>.shap`,
`regulation.<id>`, …), a canonical typed value normalised by `canonical_fact_value` (`Decimal` for
money, UTC instants for time, case-folded tokens for controlled enums), and the rendered `display`
form the narrative may legitimately contain. The prompt offers these refs to the model, the model
asserts facts against them, and `SarQualityGate` compares assertion to catalog by typed equality —
which is what makes a claim that carries a VALID citation while stating an altered amount
detectable, instead of passing every check that existed before this release.

Key classes:
- SarEvidenceCatalog: the frozen, ref-indexed catalog of facts one draft may assert against.

Key functions:
- build_evidence_catalog: derive the catalog from a projected, PHI-free `SarModelInput`.
- required_narrative_facts: select the core facts every generated draft must trace explicitly.

Notes:
- Ref ids are positional for rules and named for drivers, aggregates, and regulations, so the same
  projected input always yields byte-identical refs (the catalog is replayable gate evidence).
- The catalog is also the trusted evidence-ref set for the single-writer path: there are no agent
  tool results there, so a claim may only reference a fact this module derived.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.sar.egress import SarModelInput
from fraudlens_ml.sar import SarEvidenceFact, SarFactKind, canonical_fact_value

_PERCENT_SCALE = 100
REQUIRED_NARRATIVE_FACT_REFS = (
    "txn.amount",
    "txn.currency",
    "txn.country",
    "txn.channel",
    "txn.direction",
    "txn.occurredAt",
    "risk.band",
    "risk.fraudProbability",
)


class SarEvidenceCatalog(BaseModel):
    """The closed, ref-indexed set of deterministic facts one draft may reference."""

    model_config = ConfigDict(
        alias_generator=None, populate_by_name=True, extra="forbid", frozen=True
    )

    facts: tuple[SarEvidenceFact, ...] = Field(
        default=(), description="Ordered catalog facts derived from the projected model input."
    )

    @property
    def refs(self) -> frozenset[str]:
        """Return the trusted evidence reference ids a claim may cite."""
        return frozenset(fact.ref for fact in self.facts)

    def get(self, ref: str) -> SarEvidenceFact | None:
        """Return the catalog fact for a reference id, or None when the ref is unknown."""
        return next((fact for fact in self.facts if fact.ref == ref), None)


def required_narrative_facts(catalog: SarEvidenceCatalog) -> tuple[SarEvidenceFact, ...]:
    """Return the ordered trusted facts every production narrative must trace explicitly."""
    by_ref = {fact.ref: fact for fact in catalog.facts}
    missing = tuple(ref for ref in REQUIRED_NARRATIVE_FACT_REFS if ref not in by_ref)
    if missing:
        raise ValueError(f"evidence catalog is missing required narrative facts: {missing}")
    return tuple(by_ref[ref] for ref in REQUIRED_NARRATIVE_FACT_REFS)


def _fact(ref: str, kind: SarFactKind, raw: str, display: str) -> SarEvidenceFact:
    """Build one catalog entry, canonicalising its value by the declared kind."""
    canonical = canonical_fact_value(kind, raw)
    if canonical is None:
        raise ValueError(f"evidence fact '{ref}' has a value that cannot be canonicalised")
    return SarEvidenceFact(ref=ref, kind=kind, value=canonical, display=display)


def build_evidence_catalog(model_input: SarModelInput) -> SarEvidenceCatalog:
    """Derive the deterministic evidence catalog from one projected, PHI-free model input."""
    return SarEvidenceCatalog(
        facts=(
            *_transaction_facts(model_input),
            *_risk_facts(model_input),
            *_rule_facts(model_input),
            *_driver_facts(model_input),
            *_aggregate_facts(model_input),
            *_regulation_facts(model_input),
        )
    )


def _transaction_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive the verified transaction facts (amount, currency, place, channel, time)."""
    transaction = model_input.transaction
    occurred_at = transaction.occurred_at.isoformat()
    return (
        _fact(
            "txn.amount",
            SarFactKind.MONEY,
            str(transaction.amount),
            f"{transaction.amount} {transaction.currency}",
        ),
        _fact("txn.currency", SarFactKind.ENUM, transaction.currency, transaction.currency),
        _fact("txn.country", SarFactKind.ENUM, transaction.country, transaction.country),
        _fact("txn.channel", SarFactKind.ENUM, transaction.channel, transaction.channel),
        _fact(
            "txn.direction",
            SarFactKind.ENUM,
            transaction.direction.value,
            transaction.direction.value,
        ),
        _fact("txn.occurredAt", SarFactKind.INSTANT, occurred_at, occurred_at),
    )


def _risk_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive the deterministic risk band and the calibrated fraud probability."""
    probability = model_input.fraud_probability
    band = model_input.risk_band.value
    return (
        _fact("risk.band", SarFactKind.ENUM, band, band),
        _fact(
            "risk.fraudProbability",
            SarFactKind.NUMBER,
            f"{probability:.6f}",
            f"{probability * _PERCENT_SCALE:.1f}%",
        ),
    )


def _rule_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive one type and one severity fact per fired rule, keyed by its one-based ordinal."""
    facts: list[SarEvidenceFact] = []
    for ordinal, hit in enumerate(model_input.rule_hits, start=1):
        rule_type = hit.rule_type.value
        facts.append(_fact(f"rule.{ordinal}.type", SarFactKind.ENUM, rule_type, rule_type))
        facts.append(
            _fact(f"rule.{ordinal}.severity", SarFactKind.ENUM, hit.severity, hit.severity)
        )
    return tuple(facts)


def _driver_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive the value and signed SHAP contribution of every canonical model driver."""
    facts: list[SarEvidenceFact] = []
    for driver in model_input.shap_drivers:
        value = f"{driver.value:.6f}"
        shap_value = f"{driver.shap_value:.6f}"
        facts.append(
            _fact(f"driver.{driver.feature}.value", SarFactKind.NUMBER, value, f"{driver.value:g}")
        )
        facts.append(
            _fact(
                f"driver.{driver.feature}.shap",
                SarFactKind.NUMBER,
                shap_value,
                f"{driver.shap_value:g}",
            )
        )
    return tuple(facts)


def _aggregate_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive the backend-calculated numeric aggregates made available to drafting."""
    return tuple(
        _fact(
            f"aggregate.{aggregate.name}",
            SarFactKind.NUMBER,
            f"{aggregate.value:.6f}",
            f"{aggregate.value:g}",
        )
        for aggregate in model_input.aggregates
    )


def _regulation_facts(model_input: SarModelInput) -> tuple[SarEvidenceFact, ...]:
    """Derive one fact per digest-verified regulation excerpt, keyed by its citation id."""
    return tuple(
        _fact(
            f"regulation.{regulation.citation_id}",
            SarFactKind.ENUM,
            regulation.citation_id,
            regulation.citation_id,
        )
        for regulation in model_input.regulations
    )
