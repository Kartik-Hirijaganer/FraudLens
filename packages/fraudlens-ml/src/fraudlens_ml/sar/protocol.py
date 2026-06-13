"""Summary: The injected `SarDrafter` contract (plan §7, §16 Phase 7). This module is the
seam that lets SAR drafting reach `fraudlens-ml` WITHOUT ml ever importing `fraudlens-llm` or
`fraudlens-backend` (the ruff-enforced layering, plan §16 Phase 1): the LangGraph pipeline
(Phase 8) depends only on this protocol + its value types, and the backend supplies a concrete
mock/live drafter. It is deliberately DEPENDENCY-LIGHT — it imports `fraudlens-core` (the
PHI-free `RuleHit` + `RiskBand`) and pydantic only, never `fraudlens_ml.scoring` /
`fraudlens_ml.rag` — so importing it never drags in xgboost/shap/chromadb and the keyless mock
path stays cheap. `SarFeature` / `SarCitation` are the light value mirrors of the heavy modules'
`FeatureContribution` / `Citation`: the Phase 8 pipeline maps the SHAP + RAG outputs onto them
when it assembles a `SarInput` (and passes the already-fenced `build_rag_context` block as
`rag_context`), so this contract never touches the heavy packages. Every field is PHI-free by
construction (structured non-PHI facts + the PHI-free rule hits + SHAP feature names + escaped
regulatory citations), which is what makes "PHI masked before the prompt" hold by construction.

Key classes:
- SarDraftStatus: the lifecycle state a freshly produced draft can be in (draft | failed).
- SarEventType: the kind of streamed drafting event (token, completed, failed).
- SarFeature: one SHAP driver (feature name + value + signed contribution); PHI-free.
- SarCitation: one grounded regulatory citation (id + title + source + escaped snippet).
- SarSection: one titled section of the structured SAR body.
- SarDraftContent: the structured SAR body the drafter produces (validated, grounded).
- SarTokenUsage: normalized token usage recorded for the audit/cost trail.
- SarInput: the PHI-free assembled input a drafter turns into a SAR.
- SarDraftResult: the terminal result of a draft (content + provenance + cost + status).
- SarStreamEvent: one streamed event — a token, or the terminal completed/failed result.
- SarDrafter: the protocol the pipeline depends on; mock/live impls live in the backend.

Key functions:
- (none)

Notes:
- `SarDrafter.draft` is an async generator: it yields token events as the SAR streams, then
  exactly one terminal event whose `result.status` is `draft` (success) or `failed` (the
  provider/guardrail/schema failed — the run still completes with score+SHAP+RAG, plan §7.5).
- `SarDraftStatus` values intentionally equal the backend `SarStatus.DRAFT` / `.FAILED` string
  values so the backend repository maps the result onto `sar_drafts.status` by value without ml
  importing the backend enum (layering) — no duplicated vocabulary beyond the two reachable states.
- `SarDraftContent.cited_regulations` is the drafter's claim; the backend grounds it against
  `SarInput.citations` so a fabricated regulation id can never reach the persisted SAR (plan §8.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_core import RiskBand
from fraudlens_core.rules.base import RuleHit


class SarDraftStatus(StrEnum):
    """The state a freshly produced SAR draft can be in (maps to backend `SarStatus`)."""

    DRAFT = "draft"
    FAILED = "failed"


class SarEventType(StrEnum):
    """The kind of streamed drafting event surfaced to the pipeline/SSE layer."""

    TOKEN = "sar.token"
    COMPLETED = "sar.completed"
    FAILED = "sar.failed"


class SarFeature(BaseModel):
    """One SHAP driver surfaced in the SAR rationale (PHI-free: feature name + numbers)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    feature: str = Field(..., min_length=1, description="Feature name (no PHI).")
    value: float = Field(..., description="The feature's value for this transaction.")
    shap_value: float = Field(..., description="Signed SHAP contribution to the model margin.")


class SarCitation(BaseModel):
    """One grounded regulatory citation (already escaped/capped upstream by the RAG layer)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    citation: str = Field(
        ..., min_length=1, description="Exact regulatory id, e.g. '31 CFR 1010.314'."
    )
    title: str = Field(..., description="Title of the cited provision.")
    source: str = Field(..., description="Publisher of the provision (e.g. FinCEN / BSA).")
    snippet: str = Field(
        ..., description="Escaped, capped supporting text (injection-safe, no PHI)."
    )


class SarSection(BaseModel):
    """One titled section of the structured SAR body."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    heading: str = Field(..., min_length=1, description="Section heading.")
    body: str = Field(..., description="Section body text (PHI-masked).")


class SarDraftContent(BaseModel):
    """The structured SAR body the drafter produces — validated and citation-grounded."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )

    subject: str = Field(
        ..., min_length=1, description="One-line subject of the suspicious activity."
    )
    narrative: str = Field(..., min_length=1, description="The SAR narrative (PHI-masked).")
    sections: tuple[SarSection, ...] = Field(
        default=(),
        description="Structured supporting sections (rationale, indicators, regulation).",
    )
    cited_regulations: tuple[str, ...] = Field(
        default=(), description="Regulation ids the narrative relies on (grounded by the backend)."
    )
    recommended_action: str = Field(
        ..., min_length=1, description="Human-review recommendation (never an auto-filing action)."
    )


class SarTokenUsage(BaseModel):
    """Normalized token usage recorded on the draft for the cost/audit trail (plan §7.4)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    input_tokens: int = Field(default=0, ge=0, description="Prompt token count.")
    output_tokens: int = Field(default=0, ge=0, description="Completion token count.")
    total_tokens: int = Field(default=0, ge=0, description="Total token count.")


class SarInput(BaseModel):
    """The PHI-free assembled input a `SarDrafter` turns into a SAR draft (plan §7.8)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
    )

    agency_id: str = Field(
        ..., min_length=1, description="Owning tenant id (provenance only, not prompted)."
    )
    transaction_id: str = Field(
        ..., min_length=1, description="Transaction under investigation (id only)."
    )
    risk_band: RiskBand = Field(..., description="The blended risk band assigned to the run.")
    fraud_probability: float = Field(
        ..., ge=0.0, le=1.0, description="Calibrated model probability of fraud."
    )
    amount: Decimal = Field(..., ge=0, description="Transaction amount (non-PHI structured fact).")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    country: str = Field(
        ..., min_length=2, max_length=2, description="ISO-3166 alpha-2 country code."
    )
    channel: str = Field(..., min_length=1, description="Origination channel (e.g. 'wire').")
    model_version: str = Field(..., min_length=1, description="Scoring model version label used.")
    rules_version: str = Field(
        ..., min_length=1, description="Deterministic rules-set fingerprint."
    )
    rag_version: str = Field(..., min_length=1, description="Regulatory corpus/index version.")
    rule_hits: tuple[RuleHit, ...] = Field(
        default=(), description="The deterministic rules that fired (PHI-free findings)."
    )
    top_features: tuple[SarFeature, ...] = Field(
        default=(), description="The top SHAP drivers (feature names + numbers, no PHI)."
    )
    citations: tuple[SarCitation, ...] = Field(
        default=(), description="The grounded regulatory citations available to cite."
    )
    rag_context: str = Field(
        default="",
        description="Pre-fenced, escaped regulation block for the prompt (RAG-as-data, plan §8.1).",
    )


class SarDraftResult(BaseModel):
    """The terminal result of a draft: structured content + provenance + cost + status."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
    )

    status: SarDraftStatus = Field(..., description="draft (succeeded) or failed (plan §7.5).")
    content: str = Field(default="", description="Human-readable, PHI-masked SAR text (persisted).")
    structured: SarDraftContent | None = Field(
        default=None, description="The validated structured body, or None when the draft failed."
    )
    citations: tuple[SarCitation, ...] = Field(
        default=(), description="The grounded citations the SAR actually relied on."
    )
    model_id: str = Field(..., min_length=1, description="Model reference that produced the draft.")
    provider: str | None = Field(
        default=None, description="Resolved provider name (None for mock)."
    )
    prompt_version: str = Field(..., min_length=1, description="SAR prompt template version id.")
    prompt_hash: str = Field(
        ..., min_length=1, description="Hash of the exact prompt template used."
    )
    token_usage: SarTokenUsage = Field(
        default_factory=SarTokenUsage, description="Token usage for the call (zero for mock/cache)."
    )
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0, description="Estimated USD spend.")
    cached: bool = Field(default=False, description="Whether this result was replayed from cache.")
    fallback_count: int = Field(
        default=0,
        ge=0,
        description="1 when a fallback model served the draft, else 0 (best-effort).",
    )
    guardrail_decision: str | None = Field(
        default=None, description="Overall guardrail decision (allow|flag|block) or None for mock."
    )
    error_code: str | None = Field(
        default=None, description="Stable failure code when status == failed."
    )


class SarStreamEvent(BaseModel):
    """One streamed drafting event: a token, or the terminal completed/failed result."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )

    type: SarEventType = Field(..., description="Event kind (token | completed | failed).")
    token: str | None = Field(default=None, description="Token delta for TOKEN events, else None.")
    result: SarDraftResult | None = Field(
        default=None, description="Terminal result for COMPLETED/FAILED events, else None."
    )


@runtime_checkable
class SarDrafter(Protocol):
    """The injected SAR-drafting contract; mock/live implementations live in the backend."""

    def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Stream token events then exactly one terminal completed/failed event."""
        ...
