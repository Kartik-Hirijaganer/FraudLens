"""Summary: The investigation Runner — the run-lifecycle owner the backend launches as an
in-process background task (plan §10.2, ADR-016, §16 Phase 8). `POST /investigations` creates the
`analysis_runs` row and OWNS execution by handing the run here; the Runner emits `run.started`,
invokes the LangGraph orchestrator (`build_pipeline_graph`) to run rules→scoring→SHAP→RAG→SAR, and
then — on success — raises the conditional alert, marks the run completed (stamping the
transaction's latest run + band), and emits the terminal `run.completed`. A failure in the
DETERMINISTIC core (rules/scoring/SHAP) propagates out of the graph, so the Runner marks the run
failed and emits `run.failed{code}` with the partial event log already durable (soft RAG/LLM
failures degrade inside the graph and never reach here, plan §10.6). The Runner returns a PHI-free
`RunReport` the job runner records; the pipeline runs to completion regardless of any stream.

Key classes:
- RunReport: the PHI-free terminal outcome of a run (status + band + score + sar draft + error).
- Runner: builds the orchestration graph from the deps and drives one run to completion/failure.

Key functions:
- (none)

Notes:
- The Runner is constructed per run (its `PipelineDeps.store` is run-scoped); the backend wraps
  `run()` in a background task so the run lifecycle is decoupled from the request + any stream.
- On a deterministic-core exception the partial provenance is unknown (LangGraph drops the state),
  so `fail_run` records only the stable error code — the already-persisted events carry the detail.
- `run.completed` is always the last persisted event (highest seq), so an SSE observer tailing the
  log sees it as the terminal signal; `run.failed` plays that role on the failure path.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import RiskAssessment, RiskBand, RuleEvaluation
from fraudlens_ml.pipeline.events import (
    AlertRecord,
    PipelineDeps,
    PipelineEventType,
    PipelineInput,
    RagResult,
    RunProvenance,
    ScoreResult,
)
from fraudlens_ml.pipeline.graph import build_pipeline_graph, persist_and_emit
from fraudlens_ml.pipeline.steps import completed_payload, severity_for_band

# The stable run.failed code for a deterministic-core step failure (PHI-free, no internals leaked).
_RUN_FAILED_CODE = "investigation_failed"


class RunReport(BaseModel):
    """The PHI-free terminal outcome of an investigation run (recorded by the job runner)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(..., description="The run this report describes.")
    status: Literal["completed", "failed"] = Field(..., description="Terminal run status.")
    risk_band: RiskBand | None = Field(default=None, description="Risk band (None when failed).")
    combined_score: float | None = Field(
        default=None, description="Blended score (None when the run failed in the core)."
    )
    sar_draft_id: str | None = Field(default=None, description="Persisted SAR draft id, if any.")
    sar_status: str | None = Field(default=None, description="SAR draft status (draft|failed).")
    error_code: str | None = Field(default=None, description="Stable failure code when failed.")


class Runner:
    """Builds the orchestration graph from the injected deps and drives one run to its terminus."""

    def __init__(self, deps: PipelineDeps) -> None:
        """Bind the deps and compile the rules→scoring→SHAP→RAG→SAR orchestration graph."""
        self._deps = deps
        self._graph = build_pipeline_graph(deps)

    async def run(self, pipeline_input: PipelineInput) -> RunReport:
        """Own one run end-to-end: started → graph → completed (+alert) or failed (+partial log)."""
        deps = self._deps
        await persist_and_emit(
            deps,
            PipelineEventType.RUN_STARTED,
            {"transactionId": pipeline_input.transaction_id},
        )
        try:
            final = await self._graph.ainvoke({"pipeline_input": pipeline_input})
        except Exception:
            await deps.store.fail_run(error_code=_RUN_FAILED_CODE, provenance=RunProvenance())
            await persist_and_emit(deps, PipelineEventType.RUN_FAILED, {"code": _RUN_FAILED_CODE})
            return RunReport(
                run_id=pipeline_input.run_id, status="failed", error_code=_RUN_FAILED_CODE
            )
        return await self._complete(pipeline_input, final)

    async def _complete(self, pipeline_input: PipelineInput, final: dict[str, Any]) -> RunReport:
        """Raise the conditional alert, mark the run completed, and emit the terminal event."""
        deps = self._deps
        evaluation: RuleEvaluation = final["evaluation"]
        score: ScoreResult = final["score"]
        assessment: RiskAssessment = final["assessment"]
        rag: RagResult = final["rag"]
        sar_draft_id = str(final.get("sar_draft_id", ""))
        provenance = RunProvenance(
            model_version=score.model_version_label,
            rules_version=evaluation.rules_version,
            rag_version=rag.rag_version,
            prompt_version=final.get("sar_prompt_version"),
        )
        if assessment.alert:
            await deps.store.raise_alert(
                AlertRecord(
                    severity=severity_for_band(assessment.risk_band), risk_band=assessment.risk_band
                )
            )
        await deps.store.complete_run(
            combined_score=assessment.combined_score,
            risk_band=assessment.risk_band,
            provenance=provenance,
        )
        await persist_and_emit(
            deps,
            PipelineEventType.RUN_COMPLETED,
            completed_payload(assessment, score, sar_draft_id),
        )
        return RunReport(
            run_id=pipeline_input.run_id,
            status="completed",
            risk_band=assessment.risk_band,
            combined_score=assessment.combined_score,
            sar_draft_id=sar_draft_id or None,
            sar_status=str(final.get("sar_status")) if final.get("sar_status") else None,
        )
