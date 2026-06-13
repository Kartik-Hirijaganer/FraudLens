"""fraudlens-ml investigation pipeline (plan §16 Phase 8): the LangGraph orchestrator that
composes rules→scoring→SHAP→RAG→SAR into a persisted, idempotent run owned by `POST`, with SSE
as a pure observer/replay (ADR-016). The graph is pure over an in-memory state and reaches all
IO through injected ports (`PipelineDeps`) + an async `RunStore` the backend implements, so it
imports no heavy ML and is driven identically by real adapters or test fakes. Layering: imports
fraudlens-core + the dependency-light `fraudlens_ml.sar` contract only — never fraudlens-backend
or fraudlens-llm. Re-exports are intentional (the public pipeline surface)."""

from __future__ import annotations

from fraudlens_ml.pipeline.events import (
    AlertRecord,
    EventEmitter,
    ExplainerPort,
    InferenceRecord,
    PipelineDeps,
    PipelineEventType,
    PipelineInput,
    RagRecord,
    RagResult,
    ResultRecord,
    RetrieverPort,
    RulesPort,
    RunProvenance,
    RunStore,
    ScoreResult,
    ScorerPort,
    ShapResult,
    StreamMessage,
)
from fraudlens_ml.pipeline.graph import PipelineState, build_pipeline_graph, persist_and_emit
from fraudlens_ml.pipeline.runner import Runner, RunReport

__all__ = [
    "AlertRecord",
    "EventEmitter",
    "ExplainerPort",
    "InferenceRecord",
    "PipelineDeps",
    "PipelineEventType",
    "PipelineInput",
    "PipelineState",
    "RagRecord",
    "RagResult",
    "ResultRecord",
    "RetrieverPort",
    "RulesPort",
    "RunProvenance",
    "RunReport",
    "RunStore",
    "Runner",
    "ScoreResult",
    "ScorerPort",
    "ShapResult",
    "StreamMessage",
    "build_pipeline_graph",
    "persist_and_emit",
]
