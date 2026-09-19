"""Summary: The investigation Runner — the run-lifecycle owner the backend launches as an
in-process background task (plan §10.2, ADR-016, §16 Phase 8). `POST /investigations` creates the
`analysis_runs` row and OWNS execution by handing the run here; the Runner emits `run.started`,
invokes the LangGraph orchestrator (`build_pipeline_graph`) to run the deterministic core and the
alert-only RAG/SAR branch, and then — on success — marks the run completed (stamping the
transaction's latest run + band), and emits the terminal `run.completed`. A failure in the
DETERMINISTIC core (rules/scoring/SHAP) propagates out of the graph, so the Runner marks the run
failed and emits `run.failed{code}` with the partial event log already durable (soft RAG/LLM
failures degrade inside the graph and never reach here, plan §10.6). The Runner returns a PHI-free
`RunReport` the job runner records; the pipeline runs to completion regardless of any stream.

Key classes:
- RunReport: the PHI-free terminal outcome of a run (status + band + score + sar draft + error).
- Runner: builds the orchestration graph from the deps and drives one run to completion/failure.

Key functions:
- log_core_failure: record a core exception's TYPE, SQLSTATE, constraint and frame chain.

Notes:
- The Runner is constructed per run (its `PipelineDeps.store` is run-scoped); the backend wraps
  `run()` in a background task so the run lifecycle is decoupled from the request + any stream.
- On a deterministic-core exception the partial provenance is unknown (LangGraph drops the state),
  so `fail_run` records only the stable error code — the already-persisted events carry the detail.
- That stable code is deliberately opaque, which used to make a production failure undiagnosable:
  the handler discarded the exception entirely, so nothing recorded WHICH error was raised or
  WHERE. `log_core_failure` closes that gap without weakening the PHI invariant. It logs the
  exception's type, module and frame chain — source identity, never data — and NOT `str(exc)`,
  because messages embed inputs (a Pydantic `ValidationError` renders the offending field values)
  and the redaction net in the backend's log pipeline is pattern-based defence-in-depth, not a
  guarantee. A type plus a raise site is what locates a bug; the message is what leaks.
- The logger is named under `fraudlens.` ON PURPOSE. `configure_logging` attaches its handler to
  that logger, so a name outside the prefix would propagate to the root logger and be dropped.
  It is a plain stdlib logger because layering forbids `fraudlens-ml` importing the backend;
  `ProcessorFormatter`'s `foreign_pre_chain` picks stdlib records up regardless. For the same
  reason the fields are interpolated into the message rather than passed as `extra=`, which the
  chain has no `ExtraAdder` to render.
- `run.completed` is always the last persisted event (highest seq), so an SSE observer tailing the
  log sees it as the terminal signal; `run.failed` plays that role on the failure path.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import RiskAssessment, RiskBand, RuleEvaluation
from fraudlens_ml.pipeline.events import (
    PipelineDeps,
    PipelineEventType,
    PipelineInput,
    RagResult,
    RunProvenance,
    ScoreResult,
)
from fraudlens_ml.pipeline.graph import build_pipeline_graph, persist_and_emit
from fraudlens_ml.pipeline.steps import completed_payload

# The stable run.failed code for a deterministic-core step failure (PHI-free, no internals leaked).
_RUN_FAILED_CODE = "investigation_failed"

# Named under the backend's configured logger prefix so `configure_logging`'s handler receives it;
# see the header note. Plain stdlib — layering forbids importing the backend from this package.
_LOGGER = logging.getLogger("fraudlens.pipeline.runner")

# How many of the DEEPEST frames to name, for driver context around the raise.
_TRACEBACK_FRAME_LIMIT = 5

# `origin` reports the deepest frame belonging to OUR code, which is not the deepest frame overall.
# The first version of this reported the latter and its first real failure named `asyncpg.py:797` —
# true, useless, and three libraries below anything anyone can fix. A driver raises where it
# detects the problem; the call that caused it is further up. Match on the module rather than the
# path so a vendored copy or a renamed directory cannot silently turn our frames into foreign ones.
_FIRST_PARTY_MODULE_PREFIX = "fraudlens"

# `session.flush()` writes EVERY pending object, so the frame that raises names where the flush
# happened, not which row lost. These two fields close that gap, and BOTH are schema identity
# rather than row content: a SQLSTATE is a constant and a constraint name appears in the migration
# that created it, while the values that collided appear in neither. `str()` of any link in the
# chain stays out -- Postgres appends a DETAIL line carrying the offending key values, and the
# adapted error embeds the original's message verbatim, which is exactly the PHI withheld here.
_DBAPI_CAUSE_ATTR = "orig"
_CONSTRAINT_ATTR = "constraint_name"

# The constraint is TWO links down, not one. The asyncpg dialect translates the driver error into
# its own adapted DBAPI class and re-raises `from` the original, so `exc.orig` is SQLAlchemy's
# wrapper -- which carries the SQLSTATE the dialect copies onto it, but no constraint. Only the
# asyncpg exception at `__cause__` knows the constraint. Logging `exc.orig` alone returned
# `constraint=none` against a real unique violation, which is how this bound came to be measured.
_SQLSTATE_ATTRS = ("sqlstate", "pgcode")
_CAUSE_CHAIN_LIMIT = 4


def _constraint_name(cause: BaseException | None) -> str:
    """Walk a bounded cause chain for the driver's constraint name; 'none' when absent."""
    node: BaseException | None = cause
    for _ in range(_CAUSE_CHAIN_LIMIT):
        if node is None:
            break
        name = getattr(node, _CONSTRAINT_ATTR, None)
        if name:
            return str(name)
        node = getattr(node, "__cause__", None)
    return "none"


def log_core_failure(exc: BaseException, *, run_id: str) -> None:
    """Record a core failure's type, constraint and frame chain — never `str(exc)` (PHI)."""
    frames: list[str] = []
    origin = "unknown"
    traceback = exc.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        frames.append(f"{os.path.basename(code.co_filename)}:{traceback.tb_lineno}")
        module = str(traceback.tb_frame.f_globals.get("__name__", ""))
        if module.startswith(_FIRST_PARTY_MODULE_PREFIX):
            origin = f"{module}:{traceback.tb_lineno}"
        traceback = traceback.tb_next
    cause = getattr(exc, _DBAPI_CAUSE_ATTR, None)
    sqlstate = next(
        (str(state) for attr in _SQLSTATE_ATTRS if (state := getattr(cause, attr, None))), "none"
    )
    _LOGGER.error(
        "run.failed code=%s run_id=%s error_type=%s error_module=%s cause_type=%s sqlstate=%s "
        "constraint=%s origin=%s frames=%s",
        _RUN_FAILED_CODE,
        run_id,
        type(exc).__name__,
        type(exc).__module__,
        type(cause).__name__ if cause is not None else "none",
        sqlstate,
        _constraint_name(cause),
        origin,
        ">".join(frames[-_TRACEBACK_FRAME_LIMIT:]) or "unknown",
    )


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
        """Bind deps and compile the core plus conditional alert-enrichment graph."""
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
        except Exception as exc:
            log_core_failure(exc, run_id=pipeline_input.run_id)
            await deps.store.fail_run(error_code=_RUN_FAILED_CODE, provenance=RunProvenance())
            await persist_and_emit(deps, PipelineEventType.RUN_FAILED, {"code": _RUN_FAILED_CODE})
            return RunReport(
                run_id=pipeline_input.run_id, status="failed", error_code=_RUN_FAILED_CODE
            )
        return await self._complete(pipeline_input, final)

    async def _complete(self, pipeline_input: PipelineInput, final: dict[str, Any]) -> RunReport:
        """Complete the run with optional enrichment provenance and emit the terminal event."""
        deps = self._deps
        evaluation: RuleEvaluation = final["evaluation"]
        score: ScoreResult = final["score"]
        assessment: RiskAssessment = final["assessment"]
        rag: RagResult | None = final.get("rag")
        sar_draft_id = str(final.get("sar_draft_id", ""))
        provenance = RunProvenance(
            model_version=score.model_version_label,
            rules_version=evaluation.rules_version,
            rag_version=rag.rag_version if rag is not None else None,
            prompt_version=final.get("sar_prompt_version"),
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
