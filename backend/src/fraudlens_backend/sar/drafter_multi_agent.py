"""Summary: SarDrafter adapter for the bounded four-agent investigation graph.
The adapter streams agent lifecycle events as they occur, rolls up agent usage
and cost, grounds citations only after review, and emits one terminal draft or
failure through the existing pipeline seam.

Key classes:
- MultiAgentSarDrafter: graph-backed `SarDrafter` implementation.

Key functions:
- (none)

Notes:
- `review_unresolved` and `review_unavailable` still produce drafts pending human review.
- The outer workflow timeout yields a safe failed draft and never exposes an exception detail.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import cast

from fraudlens_backend.agents.config import AgentRole, AgentsConfig
from fraudlens_backend.agents.graph import AgentGraph, AgentGraphResult
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.runtime import AgentBudgetExceededError
from fraudlens_backend.sar.budget import BudgetGuard, SarBudgetExceededError
from fraudlens_backend.sar.schema import ground_citations, render_markdown
from fraudlens_backend.sar.streaming import stream_result
from fraudlens_llm import GuardrailDecision
from fraudlens_ml.sar import (
    SarCitation,
    SarClaim,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarInput,
    SarStreamEvent,
    SarTokenUsage,
)

_WORKFLOW = "multi_agent"
_WORKFLOW_TIMEOUT = "agent_workflow_timeout"
_WORKFLOW_ERROR = "agent_workflow_error"
_QUEUE_END = object()


class MultiAgentSarDrafter:
    """Run the bounded graph and adapt its typed outcome to the shared drafter protocol."""

    def __init__(
        self,
        *,
        graph: AgentGraph,
        config: AgentsConfig,
        prompts: dict[AgentRole, AgentPromptTemplate],
        budget: BudgetGuard,
    ) -> None:
        """Bind the compiled graph, frozen config, prompts, and per-run budget guard."""
        self._graph = graph
        self._config = config
        self._prompts = prompts.copy()
        self._budget = budget

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Stream graph lifecycle events followed by one rendered terminal result."""
        queue: asyncio.Queue[SarStreamEvent | object] = asyncio.Queue()
        failure: BaseException | None = None

        async def emit(event: SarStreamEvent) -> None:
            """Forward one graph event to the consumer without persisting in this layer."""
            await queue.put(event)

        async def drive() -> None:
            """Drive the graph under its workflow bound and enqueue its terminal rendering."""
            nonlocal failure
            try:
                self._budget.ensure_within_budget()
                async with asyncio.timeout(self._config.workflow.workflow_timeout_s):
                    graph_result = await self._graph.run(sar_input, emit=emit)
                result = self._build_result(graph_result, sar_input)
                self._budget.record(result.cost_usd)
                async for event in stream_result(result):
                    await queue.put(event)
            except (AgentBudgetExceededError, SarBudgetExceededError) as exc:
                failure = exc
            except TimeoutError:
                async for event in stream_result(self._failed_result(_WORKFLOW_TIMEOUT)):
                    await queue.put(event)
            except Exception:
                async for event in stream_result(self._failed_result(_WORKFLOW_ERROR)):
                    await queue.put(event)
            finally:
                await queue.put(_QUEUE_END)

        task = asyncio.create_task(drive())
        while True:
            item = await queue.get()
            if item is _QUEUE_END:
                break
            yield cast(SarStreamEvent, item)
        await task
        if failure is not None:
            raise failure

    def _build_result(self, graph_result: AgentGraphResult, sar_input: SarInput) -> SarDraftResult:
        """Roll up executions and ground the reviewed writer output for persistence."""
        writer_records = tuple(
            record for record in graph_result.executions if record.agent is AgentRole.SAR_WRITER
        )
        writer = writer_records[-1] if writer_records else None
        if graph_result.content is None or writer is None:
            error_code = writer.error_code if writer is not None else _WORKFLOW_ERROR
            return self._failed_result(error_code or _WORKFLOW_ERROR, graph_result=graph_result)

        content, citations = _ground_reviewed_content(graph_result.content, sar_input)
        executions = graph_result.executions
        cost = sum((record.cost_usd for record in executions), start=Decimal("0"))
        requested_models = {role: self._config.agents.for_role(role).model for role in AgentRole}
        fallback_count = sum(
            record.model_id != requested_models[record.agent] for record in executions
        )
        return SarDraftResult(
            status=SarDraftStatus.DRAFT,
            content=render_markdown(content),
            structured=content,
            citations=citations,
            model_id=writer.model_id,
            provider=_provider_from_ref(writer.model_id),
            prompt_version=writer.prompt_version,
            prompt_hash=writer.prompt_hash,
            token_usage=SarTokenUsage(
                input_tokens=sum(record.input_tokens for record in executions),
                output_tokens=sum(record.output_tokens for record in executions),
                total_tokens=sum(record.total_tokens for record in executions),
            ),
            cost_usd=cost,
            fallback_count=fallback_count,
            guardrail_decision=_strictest_guardrail(graph_result),
            workflow=_WORKFLOW,
            revision_count=graph_result.revision_count,
        )

    def _failed_result(
        self,
        error_code: str,
        *,
        graph_result: AgentGraphResult | None = None,
    ) -> SarDraftResult:
        """Build a stable failed result while retaining any completed agent usage."""
        executions = graph_result.executions if graph_result is not None else ()
        writer = next(
            (record for record in reversed(executions) if record.agent is AgentRole.SAR_WRITER),
            None,
        )
        prompt = self._prompts[AgentRole.SAR_WRITER]
        model_id = writer.model_id if writer is not None else self._config.agents.sar_writer.model
        return SarDraftResult(
            status=SarDraftStatus.FAILED,
            model_id=model_id,
            provider=_provider_from_ref(model_id),
            prompt_version=writer.prompt_version if writer is not None else prompt.prompt_version,
            prompt_hash=writer.prompt_hash if writer is not None else prompt.prompt_hash,
            token_usage=SarTokenUsage(
                input_tokens=sum(record.input_tokens for record in executions),
                output_tokens=sum(record.output_tokens for record in executions),
                total_tokens=sum(record.total_tokens for record in executions),
            ),
            cost_usd=sum(
                (record.cost_usd for record in executions),
                start=Decimal("0"),
            ),
            guardrail_decision=(
                _strictest_guardrail(graph_result) if graph_result is not None else None
            ),
            error_code=error_code,
            workflow=_WORKFLOW,
            revision_count=graph_result.revision_count if graph_result is not None else 0,
        )


def _ground_reviewed_content(
    content: SarDraftContent,
    sar_input: SarInput,
) -> tuple[SarDraftContent, tuple[SarCitation, ...]]:
    """Drop unavailable ids only after review, including ids carried by individual claims."""
    grounded_ids, grounded = ground_citations(content.cited_regulations, sar_input.citations)
    available_ids = {citation.citation for citation in sar_input.citations}
    claims = tuple(
        SarClaim(
            statement=claim.statement,
            evidence_refs=claim.evidence_refs,
            citation_ids=tuple(item for item in claim.citation_ids if item in available_ids),
        )
        for claim in content.claims
    )
    return content.model_copy(
        update={"cited_regulations": grounded_ids, "claims": claims}
    ), grounded


def _provider_from_ref(model_ref: str) -> str | None:
    """Return the catalog provider prefix when present."""
    provider, separator, _model_id = model_ref.partition("/")
    return provider if separator else None


def _strictest_guardrail(graph_result: AgentGraphResult) -> str | None:
    """Return the strictest recorded guardrail decision across every agent call."""
    rank = {
        GuardrailDecision.ALLOW: 0,
        GuardrailDecision.FLAG: 1,
        GuardrailDecision.BLOCK: 2,
    }
    decisions = tuple(
        record.guardrail_decision
        for record in graph_result.executions
        if record.guardrail_decision is not None
    )
    return max(decisions, key=rank.__getitem__).value if decisions else None
