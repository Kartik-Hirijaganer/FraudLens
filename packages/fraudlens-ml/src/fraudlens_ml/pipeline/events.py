"""Summary: The PHI-free value types + injected ports the LangGraph investigation pipeline is
built on (plan §16 Phase 8, ADR-016). This module is what keeps the pipeline import-light and
testable WITHOUT the heavy ML stack: it imports `fraudlens-core` (RuleContext/RuleEvaluation/
RiskBand/RuleHit) and the dependency-light `fraudlens_ml.sar` contract (SarFeature/SarCitation/
SarInput/SarDraftResult) + pydantic only — never `fraudlens_ml.scoring`/`fraudlens_ml.rag`, so
importing the pipeline never drags in xgboost/shap/chromadb. The scorer, explainer, and retriever
are reached through structural PROTOCOLS that return these light result types, and the backend
adapts the real heavy implementations onto them (so a test passes fakes; plan "pure nodes +
injected IO"). `RunStore` is the async persistence boundary the backend implements over the DB
repos, and `EventEmitter` is the best-effort live broadcast the SSE observer tails — persisted
ordered step events back replay from `Last-Event-ID`, while ephemeral `sar.token`s only emit.

Key classes:
- PipelineEventType: the persisted ordered step-event types (values mirror the backend enum).
- StreamMessage: one broadcast unit (a persisted step event with a seq, or an ephemeral token).
- ScoreResult: the light scorer output (probability + version label + canary flag).
- ShapResult: the light explainer output (base value + contributions + top SHAP drivers).
- RagResult: the light retriever output (citations + fenced context + mode + chunks to persist).
- PipelineInput: the PHI-free per-run input the pipeline consumes (context + non-PHI facts).
- ResultRecord: the immutable `analysis_results` snapshot the deterministic core persists.
- InferenceRecord: the hash-only `model_inference_logs` record for the scoring step.
- RagRecord: the `rag_retrievals` record (query + top-k + chunks + corpus version).
- RunProvenance: the per-step version provenance stamped on `analysis_runs`.
- AlertRecord: the conditional `alerts` row raised when the score crosses the threshold.
- RulesPort: the injected deterministic-rules collaborator.
- ScorerPort: the injected active-model scoring collaborator.
- ExplainerPort: the injected SHAP explanation collaborator.
- RetrieverPort: the injected FinCEN/BSA retrieval collaborator.
- RunStore: the async persistence port the backend implements over the DB repositories.
- PipelineDeps: the bundle of injected ports + store + emit + risk policy the graph runs on.

Key functions:
- (none)

Notes:
- `PipelineEventType` values are the dotted strings the backend `AnalysisRunEventType` stores, so
  the backend maps events onto persisted rows by VALUE without the pipeline importing the backend
  enum (the same value-equality trick `SarDraftStatus` uses for layering, plan §9.1).
- `StreamMessage.seq` is the persisted ordering key for replay; it is `None` for `sar.token`s,
  which stream live but are never persisted (the authoritative SAR lands in `sar_drafts`).
- Every payload/record is PHI-free by construction (rule hits, SHAP feature names, escaped
  citations, structured non-PHI facts) — which is what makes the persisted event log safe (§9.1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from fraudlens_core import RiskBand, RiskPolicy, RuleContext, RuleEvaluation
from fraudlens_ml.sar import SarCitation, SarDrafter, SarDraftResult, SarFeature

_DEFAULT_RAG_TOP_K = 4

# The live-only token event name (not a persisted step type; mirrors fraudlens_ml.sar.SarEventType).
SAR_TOKEN_EVENT = "sar.token"


class PipelineEventType(StrEnum):
    """Persisted ordered step-event types (values mirror the backend `AnalysisRunEventType`)."""

    RUN_STARTED = "run.started"
    STEP_RULES_COMPLETED = "step.rules.completed"
    STEP_SCORING_COMPLETED = "step.scoring.completed"
    STEP_SHAP_COMPLETED = "step.shap.completed"
    STEP_RAG_COMPLETED = "step.rag.completed"
    SAR_STARTED = "sar.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class StreamMessage(BaseModel):
    """One broadcast unit: a persisted step event (with seq) or an ephemeral token (seq None)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str = Field(
        ..., description="Event name (a PipelineEventType value or 'sar.token')."
    )
    seq: int | None = Field(
        default=None, description="Persisted ordering key for replay; None for live-only tokens."
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="PHI-free event payload (camelCase keys for the SSE wire).",
    )


class ScoreResult(BaseModel):
    """The light scorer output the pipeline consumes (no xgboost types cross the boundary)."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    fraud_probability: float = Field(..., ge=0.0, le=1.0, description="Calibrated P(fraud).")
    model_version_label: str = Field(..., description="Version label of the model that scored.")
    was_canary: bool = Field(
        default=False, description="True when a canary model scored (Phase 10; active-only in v1)."
    )


class ShapResult(BaseModel):
    """The light SHAP explainer output (base value + contributions + top drivers, PHI-free)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_value: float = Field(..., description="Explainer expected value (margin at baseline).")
    shap_values: dict[str, float] = Field(
        default_factory=dict,
        description="Per-feature signed contributions (feature name -> value).",
    )
    top_features: tuple[SarFeature, ...] = Field(
        default=(), description="The highest-|contribution| drivers, most important first."
    )


class RagResult(BaseModel):
    """The light retriever output: grounded citations, the fenced prompt block, and chunks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citations: tuple[SarCitation, ...] = Field(
        default=(), description="Deduplicated, escaped regulatory citations for the run."
    )
    rag_context: str = Field(
        default="", description="Pre-fenced, escaped regulation block for the prompt (RAG-as-data)."
    )
    mode: str = Field(
        default="empty", description="Retrieval degradation mode: 'vector' | 'lexical' | 'empty'."
    )
    rag_version: str = Field(..., description="Corpus/index version recorded on the run.")
    chunks: tuple[dict[str, Any], ...] = Field(
        default=(), description="PHI-free chunk records persisted to `rag_retrievals.chunks`."
    )


class PipelineInput(BaseModel):
    """The PHI-free per-run input the pipeline consumes (context + non-PHI structured facts)."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", protected_namespaces=(), arbitrary_types_allowed=True
    )

    agency_id: str = Field(..., min_length=1, description="Owning tenant id (provenance only).")
    run_id: str = Field(..., min_length=1, description="The persisted run this pipeline drives.")
    transaction_id: str = Field(..., min_length=1, description="Transaction under investigation.")
    rule_context: RuleContext = Field(
        ..., description="PHI-free transaction + same-account history for rules + features."
    )
    amount: Decimal = Field(..., ge=0, description="Transaction amount (non-PHI structured fact).")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code.")
    country: str = Field(..., min_length=2, max_length=2, description="ISO-3166 alpha-2 country.")
    channel: str = Field(..., min_length=1, description="Origination channel (e.g. 'wire').")
    feature_hash: str = Field(
        ..., min_length=1, description="PHI-free content fingerprint (hash-only inference log)."
    )


class ResultRecord(BaseModel):
    """The immutable `analysis_results` snapshot the deterministic core persists (PHI-free)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, protected_namespaces=()
    )

    fraud_probability: float = Field(..., ge=0.0, le=1.0, description="Calibrated model P(fraud).")
    shap_values: dict[str, float] = Field(
        default_factory=dict, description="Per-feature SHAP contributions (feature name -> value)."
    )
    top_features: list[dict[str, Any]] = Field(
        default_factory=list, description="Top SHAP drivers as camelCase JSON (feature/value/shap)."
    )
    rule_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="Fired rule hits as JSON (PHI-free reasons + counts)."
    )
    combined_score: float = Field(..., ge=0.0, le=1.0, description="Blended rules+model score.")
    risk_band: RiskBand = Field(..., description="Band resolved from the combined score.")
    model_version: str = Field(..., min_length=1, description="Scoring model version label used.")


class InferenceRecord(BaseModel):
    """A hash-only `model_inference_logs` record — never PHI (plan §9.2, ADR-015)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, protected_namespaces=()
    )

    model_version_label: str = Field(..., min_length=1, description="Version label that scored.")
    was_canary: bool = Field(..., description="Whether the canary model produced this inference.")
    fraud_probability: float = Field(..., ge=0.0, le=1.0, description="Calibrated P(fraud).")
    feature_hash: str = Field(
        ..., min_length=1, description="PHI-free content fingerprint (no PHI)."
    )


class RagRecord(BaseModel):
    """A `rag_retrievals` record: the query, top-k, the retrieved chunks, and the corpus version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(..., description="The PHI-free retrieval query built from the run signals.")
    top_k: int = Field(..., ge=0, description="How many chunks were requested.")
    chunks: list[dict[str, Any]] = Field(
        default_factory=list, description="The retrieved chunk records (citations + escaped text)."
    )
    rag_version: str = Field(..., min_length=1, description="Corpus/index version used.")


class RunProvenance(BaseModel):
    """The per-step version provenance stamped onto `analysis_runs` (filled as steps complete)."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model_version: str | None = Field(default=None, description="Scoring model version label.")
    rules_version: str | None = Field(default=None, description="Deterministic rules fingerprint.")
    rag_version: str | None = Field(default=None, description="Regulatory corpus/index version.")
    prompt_version: str | None = Field(default=None, description="SAR prompt template version.")


class AlertRecord(BaseModel):
    """The conditional `alerts` row raised when the combined score crosses the alert threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: str = Field(..., min_length=1, description="Alert severity derived from the band.")
    risk_band: RiskBand = Field(..., description="The run's risk band (provenance for the alert).")


@runtime_checkable
class RulesPort(Protocol):
    """Evaluates the deterministic rules engine for a context (backend binds the rule set)."""

    def evaluate(self, context: RuleContext) -> RuleEvaluation:
        """Return the fired hits + weighted subscore + version for the context."""
        ...


@runtime_checkable
class ScorerPort(Protocol):
    """Scores a context via the active (pointer-resolved) model (backend binds the pointer)."""

    def score(self, context: RuleContext) -> ScoreResult:
        """Return the calibrated probability + the version label that scored."""
        ...


@runtime_checkable
class ExplainerPort(Protocol):
    """Explains a context as additive SHAP contributions (backend binds the loaded artifact)."""

    def explain(self, context: RuleContext) -> ShapResult:
        """Return the base value, per-feature contributions, and the top drivers."""
        ...


@runtime_checkable
class RetrieverPort(Protocol):
    """Retrieves grounded FinCEN/BSA citations + the fenced prompt context for a query."""

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        """Return the citations, the escaped fenced context, the mode, and the chunks to persist."""
        ...


@runtime_checkable
class RunStore(Protocol):
    """The async persistence boundary the backend implements over the DB repositories.

    Implementations COMMIT incrementally (per call) so a mid-pipeline failure still leaves the
    partial event log + deterministic-core result durable for SSE replay (ADR-016 / plan §10.6).
    """

    async def append_event(self, event_type: PipelineEventType, payload: dict[str, Any]) -> int:
        """Persist the next ordered `analysis_run_events` row and return its `seq`."""
        ...

    async def save_result(self, record: ResultRecord) -> None:
        """Persist the immutable `analysis_results` snapshot for the run."""
        ...

    async def log_inference(self, record: InferenceRecord) -> None:
        """Persist the hash-only `model_inference_logs` row for the scoring step."""
        ...

    async def save_rag(self, record: RagRecord) -> None:
        """Persist the `rag_retrievals` row for the run."""
        ...

    async def save_sar(self, result: SarDraftResult) -> str:
        """Persist the SAR draft (draft or failed) and return its id."""
        ...

    async def raise_alert(self, record: AlertRecord) -> None:
        """Persist the conditional open `alerts` row when the threshold is crossed."""
        ...

    async def complete_run(
        self, *, combined_score: float, risk_band: RiskBand, provenance: RunProvenance
    ) -> None:
        """Mark the run completed and stamp the transaction's latest run + band."""
        ...

    async def fail_run(self, *, error_code: str, provenance: RunProvenance) -> None:
        """Mark the run failed (deterministic-core failure) with a stable error code."""
        ...


# A best-effort live broadcast: the backend fans a StreamMessage out to SSE subscribers (or
# no-ops when none are attached). Persisted events also emit so the observer tails without polling.
EventEmitter = Callable[[StreamMessage], Awaitable[None]]


@dataclass(frozen=True)
class PipelineDeps:
    """The injected collaborators + config the orchestration graph and Runner are built on.

    Bundled once by the backend per run (`pipeline_wiring`); the per-run `PipelineInput` flows
    through the graph state instead, so the same deps could drive many runs. A dataclass (not a
    Pydantic model) because it holds protocol instances + a callback, not a serializable boundary.
    """

    rules: RulesPort
    scorer: ScorerPort
    explainer: ExplainerPort
    retriever: RetrieverPort
    drafter: SarDrafter
    store: RunStore
    emit: EventEmitter
    risk_policy: RiskPolicy
    rag_top_k: int = _DEFAULT_RAG_TOP_K
