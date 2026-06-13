"""Summary: The LangGraph investigation orchestrator (plan §3.3, §10.2, §16 Phase 8). It wires
the five discrete steps — rules → scoring → SHAP → RAG → SAR — into a `StateGraph` whose nodes
are pure over the accumulating `PipelineState` and reach all IO through the injected `PipelineDeps`
(scorer / explainer / retriever / drafter ports + the `RunStore` + the live `emit`), so the graph
itself imports no heavy ML and is driven identically by real adapters or test fakes. The node order
realizes "graceful degradation around a deterministic core" (plan §10.6): the DETERMINISTIC steps
(rules, scoring, SHAP — which also blends the combined score+band and persists the immutable
`analysis_results`) raise on failure so the Runner can mark the run failed, while the SOFT enhancers
(RAG citations, the streamed SAR) catch their own failures and degrade — empty citations / a
`failed` SAR draft — so a RAG/LLM outage still yields a persisted risk decision + explanation. Each
step persists its ordered `analysis_run_events` row (the SSE replay log) and emits it live; only the
ephemeral `sar.token`s emit without persisting (the authoritative SAR lands in `sar_drafts`).

Key classes:
- PipelineState: the LangGraph state accumulated across the rules→scoring→SHAP→RAG→SAR nodes.

Key functions:
- persist_and_emit: append an ordered event (returns its seq) and broadcast it live (shared helper).
- build_pipeline_graph: compile the StateGraph that drives one investigation from the injected deps.

Notes:
- The deterministic nodes let exceptions propagate (the Runner catches → `run.failed`); the RAG and
  SAR nodes swallow exceptions so a soft-dependency outage never fails the run (plan §7.5 / §10.6).
- `analysis_results` is persisted inside the SHAP node — the moment the deterministic core
  (rules→score→SHAP→band) is complete — so the result is durable regardless of what RAG/SAR do.
- A misbehaving drafter that yields no terminal event degrades to a sentinel `failed` SAR draft
  rather than stranding the run (defensive; the mock/live drafters always yield a terminal, §7.5).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from fraudlens_core import RiskAssessment, RuleEvaluation
from fraudlens_ml.pipeline.events import (
    SAR_TOKEN_EVENT,
    InferenceRecord,
    PipelineDeps,
    PipelineEventType,
    PipelineInput,
    RagRecord,
    RagResult,
    ScoreResult,
    ShapResult,
    StreamMessage,
)
from fraudlens_ml.pipeline.steps import (
    build_rag_query,
    build_sar_input,
    rag_payload,
    result_record,
    rules_payload,
    scoring_payload,
    shap_payload,
)
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus, SarEventType, SarInput

# Fallback markers for the rare defensive degradation paths (named, not magic literals, rule 4).
_UNKNOWN_VERSION = "unknown"
_SAR_DRAFTER_ERROR = "sar_drafter_error"


class PipelineState(TypedDict, total=False):
    """The state accumulated across the orchestration nodes (held in memory; no checkpointer)."""

    pipeline_input: PipelineInput
    evaluation: RuleEvaluation
    score: ScoreResult
    shap: ShapResult
    assessment: RiskAssessment
    rag: RagResult
    sar_draft_id: str
    sar_status: str
    sar_prompt_version: str


async def persist_and_emit(
    deps: PipelineDeps, event_type: PipelineEventType, payload: dict[str, Any]
) -> int:
    """Persist an ordered run event (returns its seq) and broadcast it to live SSE observers."""
    seq = await deps.store.append_event(event_type, payload)
    await deps.emit(StreamMessage(event_type=event_type.value, seq=seq, data=payload))
    return seq


def _failed_sar_result() -> SarDraftResult:
    """Build a sentinel `failed` SAR draft for a misbehaving drafter (defensive, plan §7.5)."""
    return SarDraftResult(
        status=SarDraftStatus.FAILED,
        model_id=_UNKNOWN_VERSION,
        prompt_version=_UNKNOWN_VERSION,
        prompt_hash=_UNKNOWN_VERSION,
        error_code=_SAR_DRAFTER_ERROR,
    )


async def _drive_drafter(deps: PipelineDeps, sar_input: SarInput) -> SarDraftResult:
    """Stream the drafter's tokens live and return its terminal result (sentinel failed if none)."""
    terminal: SarDraftResult | None = None
    try:
        async for event in deps.drafter.draft(sar_input):
            if event.type == SarEventType.TOKEN and event.token is not None:
                await deps.emit(
                    StreamMessage(event_type=SAR_TOKEN_EVENT, data={"token": event.token})
                )
            elif event.result is not None:
                terminal = event.result
    except Exception:
        terminal = None
    return terminal if terminal is not None else _failed_sar_result()


def build_pipeline_graph(deps: PipelineDeps) -> Any:
    """Compile the rules→scoring→SHAP→RAG→SAR StateGraph bound to the injected deps."""

    async def node_rules(state: PipelineState) -> dict[str, Any]:
        """Deterministic rules → fired hits + weighted subscore; persists + emits the step event."""
        evaluation = deps.rules.evaluate(state["pipeline_input"].rule_context)
        await persist_and_emit(
            deps, PipelineEventType.STEP_RULES_COMPLETED, rules_payload(evaluation)
        )
        return {"evaluation": evaluation}

    async def node_scoring(state: PipelineState) -> dict[str, Any]:
        """Score via the active model; persists the hash-only inference log + the step event."""
        pipeline_input = state["pipeline_input"]
        score = deps.scorer.score(pipeline_input.rule_context)
        await deps.store.log_inference(
            InferenceRecord(
                model_version_label=score.model_version_label,
                was_canary=score.was_canary,
                fraud_probability=score.fraud_probability,
                feature_hash=pipeline_input.feature_hash,
            )
        )
        await persist_and_emit(
            deps, PipelineEventType.STEP_SCORING_COMPLETED, scoring_payload(score)
        )
        return {"score": score}

    async def node_shap(state: PipelineState) -> dict[str, Any]:
        """SHAP explain, then blend (core) + persist the immutable deterministic-core result."""
        pipeline_input = state["pipeline_input"]
        evaluation, score = state["evaluation"], state["score"]
        shap = deps.explainer.explain(pipeline_input.rule_context)
        await persist_and_emit(deps, PipelineEventType.STEP_SHAP_COMPLETED, shap_payload(shap))
        assessment = deps.risk_policy.assess(
            fraud_probability=score.fraud_probability, rules_subscore=evaluation.subscore
        )
        await deps.store.save_result(
            result_record(evaluation=evaluation, score=score, shap=shap, assessment=assessment)
        )
        return {"shap": shap, "assessment": assessment}

    async def node_rag(state: PipelineState) -> dict[str, Any]:
        """Soft enhancer: retrieve grounded citations; degrade to empty on any retriever fault."""
        pipeline_input = state["pipeline_input"]
        query = build_rag_query(state["evaluation"], pipeline_input)
        try:
            rag = deps.retriever.retrieve(query, top_k=deps.rag_top_k)
        except Exception:
            rag = RagResult(mode="empty", rag_version=_UNKNOWN_VERSION)
        await deps.store.save_rag(
            RagRecord(
                query=query,
                top_k=deps.rag_top_k,
                chunks=list(rag.chunks),
                rag_version=rag.rag_version,
            )
        )
        await persist_and_emit(deps, PipelineEventType.STEP_RAG_COMPLETED, rag_payload(rag))
        return {"rag": rag}

    async def node_sar(state: PipelineState) -> dict[str, Any]:
        """Soft enhancer: stream the SAR live, persist the draft (draft or failed), never abort."""
        sar_input = build_sar_input(
            pipeline_input=state["pipeline_input"],
            evaluation=state["evaluation"],
            score=state["score"],
            shap=state["shap"],
            rag=state["rag"],
            assessment=state["assessment"],
        )
        await persist_and_emit(deps, PipelineEventType.SAR_STARTED, {})
        result = await _drive_drafter(deps, sar_input)
        sar_id = await deps.store.save_sar(result)
        return {
            "sar_draft_id": sar_id,
            "sar_status": result.status.value,
            "sar_prompt_version": result.prompt_version,
        }

    graph = StateGraph(PipelineState)
    graph.add_node("rules", node_rules)
    graph.add_node("scoring", node_scoring)
    graph.add_node("shap", node_shap)
    graph.add_node("rag", node_rag)
    graph.add_node("sar", node_sar)
    graph.add_edge(START, "rules")
    graph.add_edge("rules", "scoring")
    graph.add_edge("scoring", "shap")
    graph.add_edge("shap", "rag")
    graph.add_edge("rag", "sar")
    graph.add_edge("sar", END)
    return graph.compile()
