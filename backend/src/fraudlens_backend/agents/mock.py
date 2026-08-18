"""Summary: Deterministic, keyless four-agent SAR workflow for local and browser validation.
It emits the same lifecycle contract as the live graph, persists normalized execution records,
and can exercise exactly one bounded writer-reviewer revision for a configured demo scenario.

Key classes:
- MockAgentTeam: deterministic multi-agent `SarDrafter` with no provider access or spend.

Key functions:
- (none)

Notes:
- The designated revision is selected by trusted run wiring from committed portfolio config.
- Completed deterministic attempts use the same locked replay contract as the live graph.
- No mock path is used as a live fallback.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from decimal import Decimal

from pydantic import JsonValue

from fraudlens_backend.agents.config import AgentRole, AgentsConfig
from fraudlens_backend.agents.contracts import (
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentToolCallRecord,
    AgentToolCallStatus,
    agent_run_id,
)
from fraudlens_backend.agents.prompts import AgentPromptTemplate
from fraudlens_backend.agents.resume import AgentExecutionReplayPort, execution_replay_context
from fraudlens_ml.sar import SarAgentEvent, SarDrafter, SarEventType, SarInput, SarStreamEvent

AgentExecutionRecorder = Callable[[AgentExecutionRecord], Awaitable[None]]
_REVISION_ATTEMPT = 2


class MockAgentTeam:
    """Emit a complete deterministic agent lifecycle and a mock-produced multi-agent SAR."""

    def __init__(  # noqa: PLR0913 - explicit trusted run collaborators.
        self,
        *,
        run_id: uuid.UUID,
        config: AgentsConfig,
        prompts: Mapping[AgentRole, AgentPromptTemplate],
        single_writer: SarDrafter,
        record_execution: AgentExecutionRecorder,
        replay: AgentExecutionReplayPort | None = None,
        request_revision: bool = False,
    ) -> None:
        """Bind trusted run context, frozen config, prompt provenance, and persistence callback."""
        self._run_id = run_id
        self._config = config
        self._prompts = dict(prompts)
        self._single_writer = single_writer
        self._record_execution = record_execution
        self._replay = replay
        self._request_revision = request_revision

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Stream all role attempts, an optional single revision, then the terminal mock SAR."""
        attempts = [
            (AgentRole.EVIDENCE_INVESTIGATOR, 1),
            (AgentRole.REGULATORY_ANALYST, 1),
            (AgentRole.SAR_WRITER, 1),
            (AgentRole.COMPLIANCE_REVIEWER, 1),
        ]
        if self._request_revision:
            attempts.extend(
                [
                    (AgentRole.SAR_WRITER, _REVISION_ATTEMPT),
                    (AgentRole.COMPLIANCE_REVIEWER, _REVISION_ATTEMPT),
                ]
            )

        async with execution_replay_context(self._replay) as completed:
            for role, attempt in attempts:
                record = self._execution(sar_input, role=role, attempt=attempt)
                replayed = completed.get((role, attempt))
                if replayed is not None and replayed.input_hash == record.input_hash:
                    continue
                if (
                    self._request_revision
                    and role is AgentRole.SAR_WRITER
                    and attempt == _REVISION_ATTEMPT
                ):
                    yield self._event(
                        SarEventType.AGENT_REVISION_REQUESTED,
                        role,
                        attempt,
                        status="revision_requested",
                    )
                yield self._event(SarEventType.AGENT_STARTED, role, attempt, status="started")
                await self._record_execution(record)
                for tool_call in record.tool_calls:
                    yield self._event(
                        SarEventType.AGENT_TOOL_COMPLETED,
                        role,
                        attempt,
                        status=tool_call.status.value,
                        tool_name=tool_call.name,
                    )
                yield self._event(
                    SarEventType.AGENT_COMPLETED,
                    role,
                    attempt,
                    status=record.status.value,
                )

        async for event in self._single_writer.draft(sar_input):
            if event.result is None:
                yield event
                continue
            yield event.model_copy(
                update={
                    "result": event.result.model_copy(
                        update={
                            "workflow": "multi_agent",
                            "revision_count": 1 if self._request_revision else 0,
                        }
                    )
                }
            )

    def _execution(
        self, sar_input: SarInput, *, role: AgentRole, attempt: int
    ) -> AgentExecutionRecord:
        """Build one zero-cost normalized execution with deterministic hashes and tool records."""
        prompt = self._prompts[role]
        input_hash = _hash_json(
            {
                "runId": str(self._run_id),
                "transactionId": sar_input.transaction_id,
                "agent": role.value,
                "attempt": attempt,
            }
        )
        result: dict[str, JsonValue] = {
            "mode": "mock",
            "outcome": (
                "revise"
                if self._request_revision and role is AgentRole.COMPLIANCE_REVIEWER and attempt == 1
                else "completed"
            ),
        }
        tools = self._config.agents.for_role(role).tools
        tool_calls = tuple(
            AgentToolCallRecord(
                call_id=f"mock-{role.value}-{attempt}-{index}",
                name=name,
                arguments={},
                status=AgentToolCallStatus.COMPLETED,
                result={"mode": "mock"},
            )
            for index, name in enumerate(tools, start=1)
        )
        return AgentExecutionRecord(
            agent=role,
            attempt=attempt,
            status=AgentExecutionStatus.COMPLETED,
            model_id="mock",
            prompt_version=prompt.prompt_version,
            prompt_hash=prompt.prompt_hash,
            input_hash=input_hash,
            result_hash=_hash_json(result),
            latency_ms=0,
            model_call_count=1,
            cost_usd=Decimal("0"),
            result=result,
            tool_calls=tool_calls,
        )

    def _event(
        self,
        event_type: SarEventType,
        role: AgentRole,
        attempt: int,
        *,
        status: str,
        tool_name: str | None = None,
    ) -> SarStreamEvent:
        """Build one lifecycle event using the persisted attempt's stable identity."""
        return SarStreamEvent(
            type=event_type,
            agent=SarAgentEvent(
                agent_run_id=agent_run_id(self._run_id, role, attempt),
                agent=role.value,
                attempt=attempt,
                status=status,
                tool_name=tool_name,
            ),
        )


def _hash_json(value: object) -> str:
    """Hash a canonical JSON value for deterministic mock provenance."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
