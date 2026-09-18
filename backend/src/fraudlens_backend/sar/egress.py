"""Summary: Provenance-derived, synthetic-only model-egress projection for every SAR LLM path.
The policy turns a broad internal SarInput into a frozen allowlisted SarModelInput, verifies public
regulation snippets against the committed corpus, replaces database identifiers in agent tool
results with case-scoped aliases, and rejects forbidden outbound bytes before provider access.

Key classes:
- SarModelTransaction:
- SarModelAggregate:
- SarModelRuleHit:
- SarModelDriver:
- SarModelRegulation:
- SarModelInput: the exact closed schema that prompt builders may serialize.
- PatternRule:
- AliasPolicy:
- RegulationCorpusPolicy:
- EgressPolicy: validated non-secret allowlist and detector configuration.
- EgressBlockedError: safe, stable refusal raised before a model request.

Key functions:
- load_egress_policy: load config/llm/egress.yml and bind its committed corpus.
- project_for_model: derive eligibility from recorded source and build SarModelInput.
- project_agent_tool_result: allowlist and alias an agent tool result before resubmission.
- sanitize_model_payload: mask and scrub model output before another model receives it.

Notes:
- A caller-provided data-class label is never accepted; source-to-class mapping comes only from
policy and the persisted transaction source carried by SarInput.
- Approved regulation excerpts are reconstructed from citations only after their exact escaped
snippet digest matches a chunk from the committed corpus. There is no second pre-rendered
regulation block to project: `SarInput.citations` is the only carrier (release 0.5.0 Phase 5).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator
from pydantic.alias_generators import to_camel

from fraudlens_backend.settings import find_config_dir
from fraudlens_core import AmlRuleType, RiskBand, TransactionDirection
from fraudlens_core.phi import mask_text
from fraudlens_ml.rag.citations import escape_as_data
from fraudlens_ml.rag.ingest import chunk_corpus, load_corpus
from fraudlens_ml.sar import SarInput
from fraudlens_ml.scoring import FEATURE_NAMES

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
_MODEL_FIELDS = frozenset(
    {
        "caseAlias",
        "subjectAlias",
        "counterpartyAlias",
        "transaction",
        "aggregates",
        "riskBand",
        "fraudProbability",
        "ruleHits",
        "shapDrivers",
        "regulations",
        "unknowns",
    }
)


class SarModelTransaction(BaseModel):
    """One identifier-free, verified transaction fact set."""

    model_config = _MODEL_CONFIG
    amount: Decimal = Field(..., ge=0, description="Verified transaction amount.")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code.")
    country: str = Field(..., min_length=2, max_length=2, description="ISO country code.")
    channel: str = Field(..., min_length=1, description="Controlled channel or unknown.")
    direction: TransactionDirection = Field(..., description="Subject-relative fund direction.")
    occurred_at: datetime = Field(..., description="Verified transaction occurrence time.")


class SarModelAggregate(BaseModel):
    """One backend-calculated numeric aggregate made available to drafting."""

    model_config = _MODEL_CONFIG
    name: str = Field(..., min_length=1, description="Controlled aggregate name.")
    value: float = Field(..., description="Backend-calculated aggregate value.")


class SarModelRuleHit(BaseModel):
    """One controlled rule finding with no database code or caller text."""

    model_config = _MODEL_CONFIG
    rule_type: AmlRuleType = Field(..., description="Canonical AML rule vocabulary member.")
    severity: str = Field(..., min_length=1, description="Controlled ordinal severity.")
    reason: str = Field(..., min_length=1, description="Policy-owned reason template.")


class SarModelDriver(BaseModel):
    """One numeric SHAP driver restricted to the served feature schema."""

    model_config = _MODEL_CONFIG
    feature: str = Field(..., min_length=1, description="Canonical served feature name.")
    value: float = Field(..., description="Feature value supplied to the model.")
    shap_value: float = Field(..., description="Signed SHAP contribution.")


class SarModelRegulation(BaseModel):
    """One public regulation excerpt verified against the committed corpus."""

    model_config = _MODEL_CONFIG
    citation_id: str = Field(..., min_length=1, description="Exact regulation citation id.")
    title: str = Field(..., min_length=1, description="Committed provision title.")
    source: str = Field(..., min_length=1, description="Committed public publisher.")
    snippet: str = Field(..., min_length=1, description="Escaped committed excerpt.")
    snippet_digest: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="SHA-256 of the exact escaped snippet."
    )


class SarModelInput(BaseModel):
    """The exact frozen allowlist accepted by SAR and agent prompt builders."""

    model_config = _MODEL_CONFIG
    case_alias: str = Field(..., min_length=1, description="Case-scoped non-database alias.")
    subject_alias: str = Field(..., min_length=1, description="Subject account alias.")
    counterparty_alias: str = Field(..., min_length=1, description="Counterparty account alias.")
    transaction: SarModelTransaction = Field(..., description="Verified transaction facts.")
    aggregates: tuple[SarModelAggregate, ...] = Field(
        default=(), description="Backend-calculated numeric aggregates."
    )
    risk_band: RiskBand = Field(..., description="Deterministic combined risk band.")
    fraud_probability: float = Field(..., ge=0, le=1, description="Calibrated fraud probability.")
    rule_hits: tuple[SarModelRuleHit, ...] = Field(
        default=(), description="Controlled deterministic rule findings."
    )
    shap_drivers: tuple[SarModelDriver, ...] = Field(
        default=(), description="Canonical numeric SHAP drivers."
    )
    regulations: tuple[SarModelRegulation, ...] = Field(
        default=(), description="Digest-verified public regulation excerpts."
    )
    unknowns: tuple[str, ...] = Field(
        default=(), description="Fields deliberately withheld or normalized to unknown."
    )


class PatternRule(BaseModel):
    """One named deterministic forbidden-content expression."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(..., min_length=1, description="Stable detector name.")
    pattern: str = Field(..., min_length=1, description="Python regular expression.")

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        re.compile(value)
        return value


class AliasPolicy(BaseModel):
    """Static non-identifying aliases used in every model request."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    case: str = Field(..., min_length=1, description="Case alias.")
    subject: str = Field(..., min_length=1, description="Subject alias.")
    counterparty: str = Field(..., min_length=1, description="Counterparty alias.")


class RegulationCorpusPolicy(BaseModel):
    """Committed corpus location and deterministic chunk geometry."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    directory: str = Field(..., min_length=1, description="Repository-relative corpus directory.")
    chunk_size: int = Field(..., gt=0, description="Chunk size used by ingestion.")
    chunk_overlap: int = Field(..., ge=0, description="Chunk overlap used by ingestion.")
    snippet_chars: int = Field(..., gt=0, description="Escaped model snippet length cap.")


class EgressPolicy(BaseModel):
    """Strict synthetic-only projection and transport detector policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_version: str = Field(..., min_length=1, description="Auditable policy version.")
    allowed_data_classes: frozenset[str] = Field(..., description="Eligible data classes.")
    allowed_sources: frozenset[str] = Field(..., description="Eligible persisted sources.")
    source_data_classes: dict[str, str] = Field(..., description="Source-derived classifications.")
    allowed_field_set: frozenset[str] = Field(..., description="Exact SarModelInput aliases.")
    allowed_tool_fields: frozenset[str] = Field(
        ..., description="Agent tool-result field allowlist."
    )
    rule_reason_templates: dict[AmlRuleType, str] = Field(
        ..., description="Policy-owned explanation for every rule type."
    )
    aliases: AliasPolicy = Field(..., description="Case-scoped aliases.")
    safe_placeholder: str = Field(..., min_length=1, description="Replacement for unsafe text.")
    categorical_value_pattern: str = Field(..., min_length=1, description="Safe category regex.")
    forbidden_patterns: tuple[PatternRule, ...] = Field(..., description="Outbound byte detectors.")
    detectors: tuple[Literal["deterministic"], ...] = Field(
        ..., min_length=1, description="Required deterministic detector layer."
    )
    presidio: Literal["optional"] = Field(..., description="Optional Presidio layer posture.")
    regulation_corpus: RegulationCorpusPolicy = Field(..., description="Corpus digest settings.")
    corpus_root: Path = Field(..., description="Resolved repository corpus root.")


class EgressBlockedError(RuntimeError):
    """A safe model-egress refusal carrying only a stable reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_egress_policy(path: Path | None = None) -> EgressPolicy:
    """Load the model-egress policy and resolve its corpus below the repository root."""
    config_path = path or find_config_dir() / "llm" / "egress.yml"
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        repo_root = find_config_dir().resolve().parent
        corpus = (repo_root / str(raw["regulation_corpus"]["directory"])).resolve()
        if not corpus.is_relative_to(repo_root) or not corpus.is_dir():
            raise ValueError("egress regulation corpus must be a repository directory")
        policy = EgressPolicy.model_validate({**raw, "corpus_root": corpus})
    except (KeyError, OSError, TypeError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise RuntimeError(f"Model-egress policy is invalid: {config_path}") from exc
    if policy.allowed_field_set != _MODEL_FIELDS:
        raise RuntimeError("Model-egress allowed_field_set does not match SarModelInput")
    if set(policy.rule_reason_templates) != set(AmlRuleType):
        raise RuntimeError("Model-egress policy requires a template for every AML rule type")
    return policy


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _regulation_allowlist(policy: EgressPolicy) -> dict[tuple[str, str], SarModelRegulation]:
    settings = policy.regulation_corpus
    chunks = chunk_corpus(
        load_corpus(policy.corpus_root),
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    allowed: dict[tuple[str, str], SarModelRegulation] = {}
    for chunk in chunks:
        snippet = escape_as_data(chunk.text, max_chars=settings.snippet_chars)
        digest = _digest(snippet)
        allowed[(chunk.citation, digest)] = SarModelRegulation(
            citation_id=chunk.citation,
            title=chunk.title,
            source=chunk.source,
            snippet=snippet,
            snippet_digest=digest,
        )
    return allowed


def _matches_forbidden(text: str, policy: EgressPolicy) -> bool:
    return any(re.search(rule.pattern, text) is not None for rule in policy.forbidden_patterns)


def _safe_category(value: str, policy: EgressPolicy) -> str:
    return (
        value if re.fullmatch(policy.categorical_value_pattern, value) else policy.safe_placeholder
    )


def project_for_model(sar_input: SarInput, policy: EgressPolicy) -> SarModelInput:
    """Build the only model-authorized SAR input from recorded source provenance."""
    data_class = policy.source_data_classes.get(sar_input.source)
    if (
        sar_input.source not in policy.allowed_sources
        or data_class not in policy.allowed_data_classes
    ):
        raise EgressBlockedError("egress_source_not_allowed")
    unknowns: list[str] = []
    channel = _safe_category(sar_input.channel, policy)
    if channel == policy.safe_placeholder and sar_input.channel != policy.safe_placeholder:
        unknowns.append("transaction.channel")
    drivers = tuple(
        SarModelDriver(feature=item.feature, value=item.value, shap_value=item.shap_value)
        for item in sar_input.top_features
        if item.feature in FEATURE_NAMES
    )
    if len(drivers) != len(sar_input.top_features):
        unknowns.append("shapDrivers.unrecognizedFeature")
    allowed_regulations = _regulation_allowlist(policy)
    regulations: list[SarModelRegulation] = []
    for citation in sar_input.citations:
        matched = allowed_regulations.get((citation.citation, _digest(citation.snippet)))
        if matched is None or matched.title != citation.title or matched.source != citation.source:
            raise EgressBlockedError("egress_regulation_not_allowed")
        regulations.append(matched)
    projected = SarModelInput(
        case_alias=policy.aliases.case,
        subject_alias=policy.aliases.subject,
        counterparty_alias=policy.aliases.counterparty,
        transaction=SarModelTransaction(
            amount=sar_input.amount,
            currency=sar_input.currency,
            country=sar_input.country,
            channel=channel,
            direction=sar_input.direction,
            occurred_at=sar_input.occurred_at,
        ),
        risk_band=sar_input.risk_band,
        fraud_probability=sar_input.fraud_probability,
        rule_hits=tuple(
            SarModelRuleHit(
                rule_type=hit.rule_type,
                severity=_safe_category(hit.severity, policy),
                reason=policy.rule_reason_templates[hit.rule_type],
            )
            for hit in sar_input.rule_hits
        ),
        shap_drivers=drivers,
        regulations=tuple(regulations),
        unknowns=tuple(unknowns),
    )
    if _matches_forbidden(projected.model_dump_json(by_alias=True), policy):
        raise EgressBlockedError("egress_forbidden_content")
    return projected


def _alias_evidence(value: str) -> str:
    return f"case-evidence-{_digest(value)[:12]}"


def _project_value(key: str, value: JsonValue, policy: EgressPolicy) -> JsonValue:
    if key == "evidenceRef" and isinstance(value, str):
        return _alias_evidence(value)
    if isinstance(value, dict):
        return {
            nested_key: _project_value(nested_key, nested, policy)
            for nested_key, nested in value.items()
            if nested_key in policy.allowed_tool_fields
        }
    if isinstance(value, list):
        return [_project_value(key, nested, policy) for nested in value]
    if isinstance(value, str):
        masked = mask_text(value).value
        return policy.safe_placeholder if _matches_forbidden(masked, policy) else masked
    return value


def project_agent_tool_result(
    tool_name: str, value: Mapping[str, JsonValue], policy: EgressPolicy
) -> dict[str, JsonValue]:
    """Allowlist a typed tool result and replace persisted evidence ids with stable aliases."""
    projected = {
        key: _project_value(key, item, policy)
        for key, item in value.items()
        if key in policy.allowed_tool_fields
    }
    if _matches_forbidden(str(projected), policy):
        raise EgressBlockedError(f"egress_tool_result_blocked:{tool_name}")
    return projected


def sanitize_model_payload(value: Any, policy: EgressPolicy) -> Any:
    """Mask PHI-shaped spans and replace forbidden strings before model-to-model resubmission."""
    if isinstance(value, dict):
        return {key: sanitize_model_payload(item, policy) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_model_payload(item, policy) for item in value]
    if isinstance(value, str):
        masked = mask_text(value).value
        return policy.safe_placeholder if _matches_forbidden(masked, policy) else masked
    return value
