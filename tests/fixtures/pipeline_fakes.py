"""Reusable in-memory fakes for the Phase 8 investigation-pipeline tests.

These let the pipeline (Runner/graph/steps) and the backend RunStore be exercised WITHOUT the
heavy ML stack (xgboost/shap/chromadb) or a model artifact: the scorer/explainer/retriever ports
return fixed light results, the drafter streams a scripted SAR, and `FakeRunStore` records the
persistence calls in order (assigning a gap-free seq like the real store). `RecordingEmit` captures
the live broadcast so a test can assert the streamed event sequence (including `sar.token`s).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fraudlens_core import RiskBand, RuleContext, RuleEvaluation, RuleHit
from fraudlens_core.rules.base import AmlRuleType
from fraudlens_ml.pipeline import (
    AlertRecord,
    InferenceRecord,
    PipelineEventType,
    RagRecord,
    RagResult,
    ResultRecord,
    RunProvenance,
    ScoreResult,
    ShapResult,
    StreamMessage,
)
from fraudlens_ml.sar import (
    SarCitation,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarFeature,
    SarInput,
    SarStreamEvent,
)


class FakeRulesPort:
    """A RulesPort returning a fixed evaluation (or raising to simulate a core failure)."""

    def __init__(self, evaluation: RuleEvaluation | None = None, *, error: bool = False) -> None:
        self._evaluation = evaluation or RuleEvaluation(
            hits=(
                RuleHit(
                    code="structuring",
                    rule_type=AmlRuleType.STRUCTURING,
                    severity="high",
                    weight=Decimal("2.0"),
                    reason="3 sub-threshold transactions within 168h suggest structuring.",
                    details={"count": 3},
                ),
            ),
            subscore=Decimal("0.5"),
            rules_version="rules-test",
        )
        self._error = error

    def evaluate(self, context: RuleContext) -> RuleEvaluation:
        if self._error:
            raise RuntimeError("rules boom")
        return self._evaluation


class FakeScorerPort:
    """A ScorerPort returning a fixed probability (or raising to simulate a core failure)."""

    def __init__(
        self, *, probability: float = 0.9, label: str = "v-test", error: bool = False
    ) -> None:
        self._probability = probability
        self._label = label
        self._error = error

    def score(self, context: RuleContext) -> ScoreResult:
        if self._error:
            raise RuntimeError("scorer boom")
        return ScoreResult(fraud_probability=self._probability, model_version_label=self._label)


class FakeExplainerPort:
    """An ExplainerPort returning a fixed SHAP explanation."""

    def __init__(self, *, label: str = "v-test") -> None:
        self._label = label

    def explain(self, context: RuleContext) -> ShapResult:
        return ShapResult(
            base_value=0.1,
            shap_values={"amount_log": 0.4, "country_risk": -0.1},
            top_features=(
                SarFeature(feature="amount_log", value=9.2, shap_value=0.4),
                SarFeature(feature="country_risk", value=0.85, shap_value=-0.1),
            ),
        )


class FakeRetrieverPort:
    """A RetrieverPort returning fixed citations (or raising to exercise RAG degradation)."""

    def __init__(self, *, citations: bool = True, error: bool = False) -> None:
        self._citations = citations
        self._error = error

    def retrieve(self, query: str, *, top_k: int) -> RagResult:
        if self._error:
            raise RuntimeError("retriever boom")
        citations = (
            (
                SarCitation(
                    citation="31 CFR 1010.314",
                    title="Structuring",
                    source="FinCEN",
                    snippet="No person shall structure a transaction.",
                ),
            )
            if self._citations
            else ()
        )
        return RagResult(
            citations=citations,
            rag_context="<<REGS>>\n[31 CFR 1010.314] Structuring\nsafe text\n<<END>>"
            if self._citations
            else "",
            mode="vector" if self._citations else "empty",
            rag_version="rag-test",
            chunks=({"chunkId": "d::0", "citation": "31 CFR 1010.314"},) if self._citations else (),
        )


class FakeSarDrafter:
    """A SarDrafter that streams scripted tokens then a terminal draft/failed result."""

    def __init__(
        self,
        *,
        tokens: tuple[str, ...] = ("Suspicious ", "activity ", "detected."),
        status: SarDraftStatus = SarDraftStatus.DRAFT,
        prompt_version: str = "sar-v1",
        no_terminal: bool = False,
        raise_mid: bool = False,
        include_empty_token: bool = False,
    ) -> None:
        self._tokens = tokens
        self._status = status
        self._prompt_version = prompt_version
        self._no_terminal = no_terminal
        self._raise_mid = raise_mid
        self._include_empty_token = include_empty_token

    async def draft(self, sar_input: SarInput):
        if self._include_empty_token:
            yield SarStreamEvent(type=SarEventType.TOKEN, token=None)  # skipped (no value)
        for token in self._tokens:
            yield SarStreamEvent(type=SarEventType.TOKEN, token=token)
        if self._raise_mid:
            raise RuntimeError("drafter boom")  # defensive: caught, degrades to a failed draft
        if self._no_terminal:
            return
        structured = (
            SarDraftContent(
                subject="Structuring",
                narrative="The transaction shows structuring indicators.",
                recommended_action="Escalate for human review.",
            )
            if self._status is SarDraftStatus.DRAFT
            else None
        )
        result = SarDraftResult(
            status=self._status,
            content="".join(self._tokens) if self._status is SarDraftStatus.DRAFT else "",
            structured=structured,
            citations=sar_input.citations,
            model_id="mock",
            prompt_version=self._prompt_version,
            prompt_hash="hash-test",
            error_code=None if self._status is SarDraftStatus.DRAFT else "sar_failed",
        )
        event_type = (
            SarEventType.COMPLETED if self._status is SarDraftStatus.DRAFT else SarEventType.FAILED
        )
        yield SarStreamEvent(type=event_type, result=result)


class RecordingEmit:
    """Captures every StreamMessage the pipeline emits (the live broadcast under test)."""

    def __init__(self) -> None:
        self.messages: list[StreamMessage] = []

    async def __call__(self, message: StreamMessage) -> None:
        self.messages.append(message)

    @property
    def event_types(self) -> list[str]:
        """The ordered event-type names emitted (including ephemeral `sar.token`s)."""
        return [message.event_type for message in self.messages]


class FakeRunStore:
    """An in-memory RunStore recording persistence calls + assigning a gap-free event seq."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.results: list[ResultRecord] = []
        self.inferences: list[InferenceRecord] = []
        self.rags: list[RagRecord] = []
        self.sars: list[SarDraftResult] = []
        self.alerts: list[AlertRecord] = []
        self.completed: list[tuple[float, RiskBand, RunProvenance]] = []
        self.failed: list[tuple[str, RunProvenance]] = []
        self.operations: list[str] = []
        self._seq = 0

    @property
    def event_types(self) -> list[str]:
        """The ordered persisted event-type names (the SSE replay log)."""
        return [event_type for event_type, _ in self.events]

    async def append_event(self, event_type: PipelineEventType, payload: dict[str, Any]) -> int:
        self._seq += 1
        self.events.append((event_type.value, payload))
        return self._seq

    async def save_result(self, record: ResultRecord) -> None:
        self.results.append(record)

    async def log_inference(self, record: InferenceRecord) -> None:
        self.inferences.append(record)

    async def save_rag(self, record: RagRecord) -> None:
        self.operations.append("save_rag")
        self.rags.append(record)

    async def save_sar(self, result: SarDraftResult) -> str:
        self.operations.append("save_sar")
        self.sars.append(result)
        return f"sar-{len(self.sars)}"

    async def raise_alert(self, record: AlertRecord) -> None:
        self.operations.append("raise_alert")
        self.alerts.append(record)

    async def complete_run(
        self, *, combined_score: float, risk_band: RiskBand, provenance: RunProvenance
    ) -> None:
        self.operations.append("complete_run")
        self.completed.append((combined_score, risk_band, provenance))

    async def fail_run(self, *, error_code: str, provenance: RunProvenance) -> None:
        self.failed.append((error_code, provenance))
