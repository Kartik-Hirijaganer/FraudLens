"""Replay, policy, and workflow governance around the quality-gated SAR cascade (Phase 3).

Covers the "cache, tenancy, multi-agent" integration bullet of the Phase 3 test strategy. The
tenancy half lives with the rows it is about, in `test_sar_repository.py`; what is here is the two
ways a draft could reach an analyst WITHOUT the gate having judged it under the current rules:

1. **Replay.** A cached draft is an artifact produced under one exact policy, prompt, and route.
   If the key did not bind all three, tightening `config/quality.yaml` or shipping a new prompt
   would leave every previously cached draft replaying straight past the new gate — the one way a
   cache can serve something the current rules would reject. Each test below changes exactly one
   of those and proves the replay stops.
2. **The other writer.** The multi-agent workflow produces drafts too, so it runs the SAME gate;
   when its output is rejected, `LiveAgentFallbackDrafter` hands the case to the single-writer
   cascade rather than to nothing. A draft is a draft.

Everything runs over `CapturedOpenAiEndpoint`, so a cache hit is observable as a REQUEST NOT MADE
rather than as an internal flag: no socket, no GPU, no credential.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint
from quality_gates import production_gate
from sar_drafts import fabricated_citation_json, gate_passing_json

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_fallback import LiveAgentFallbackDrafter
from fraudlens_backend.sar.factory import build_sar_drafter, load_sar_llm_config
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.quality_gate import SarQualityGate, SarRuntimeGatePolicy
from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir
from fraudlens_llm import LlmClient, LlmSettings, load_catalog, load_providers
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_ml.sar import (
    SarDraftResult,
    SarDraftStatus,
    SarEventType,
    SarGateReason,
    SarInput,
    SarStreamEvent,
)

_SINGLE_STAGE_PROFILE = "awq-raw"
_OTHER_PROFILE = "bf16-baseline"


@pytest.fixture(autouse=True)
def _deny_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """AD-3.1: this suite is offline — a real connection attempt is a failure, not a slow test."""

    def _no_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline suite attempted socket IO")

    monkeypatch.setattr(socket.socket, "connect", _no_socket)


def _llm_settings() -> LlmSettings:
    """Bind the committed catalog and provider registries in a non-prod environment."""
    config_dir = find_config_dir()
    return LlmSettings(
        catalog_path=config_dir / "llm" / "catalog.yml",
        providers_path=config_dir / "llm" / "providers.yml",
        environment="dev",
    )


def _tightened_gate() -> SarQualityGate:
    """Return the committed policy with one switch changed, so its hash differs."""
    policy = production_gate().policy
    return SarQualityGate(
        SarRuntimeGatePolicy.model_validate(
            policy.model_dump() | {"policy_version": "sar-gate-v1-tightened"}
        )
    )


class _Endpoint:
    """One in-memory provider endpoint plus every drafter that has been pointed at it."""

    def __init__(self, response: str) -> None:
        """Start a capturing endpoint with no bound transports yet."""
        self.captured = CapturedOpenAiEndpoint(response)
        self._transports: list[httpx.AsyncClient] = []

    def drafter(
        self,
        *,
        cache: InMemorySarDraftCache,
        profile: str = _SINGLE_STAGE_PROFILE,
        gate: SarQualityGate | None = None,
        prompt: SarPromptTemplate | None = None,
    ):
        """Build one real drafter for a profile, sharing the supplied replay cache."""
        settings = _llm_settings()
        catalog = load_catalog(settings.catalog_path)
        providers = load_providers(settings.providers_path)
        stage = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages(profile)[0]
        client = LlmClient.from_config(catalog, providers, settings)
        adapter = client._adapter_for(client._resolve_model(stage.model, stage.connection))
        assert isinstance(adapter, OpenAiCompatibleAdapter)
        transport = httpx.AsyncClient(transport=httpx.MockTransport(self.captured))
        adapter._client = AsyncOpenAI(
            api_key="synthetic-test-value",
            base_url=f"http://{stage.connection}.test/v1",
            http_client=transport,
            max_retries=0,
        )
        self._transports.append(transport)
        return build_sar_drafter(
            AppSettings(
                environment="dev",
                llm_mode="live",
                sar_config_file="llm/sar-vllm.yml",
                sar_profile=profile,
            ),
            client=client,
            catalog=catalog,
            budget=BudgetGuard(),
            cache=cache,
            gate=gate or production_gate(),
            prompt=prompt,
        )

    async def close(self) -> None:
        """Close every transport this endpoint handed out."""
        for transport in self._transports:
            await transport.aclose()

    @property
    def requests(self) -> int:
        """Return how many requests actually reached the provider."""
        return len(self.captured.request_bodies)


async def _draft(drafter, sar_input: SarInput) -> SarDraftResult:
    """Drain one drafter and return its terminal result."""
    events = [event async for event in drafter.draft(sar_input)]
    return events[-1].result


@pytest.mark.asyncio
async def test_an_identical_request_replays_without_a_second_provider_call(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """The baseline the invalidation tests are measured against: a hit costs no request."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(gate_passing_json(sar_input))
    cache = InMemorySarDraftCache()

    try:
        drafter = endpoint.drafter(cache=cache)
        first = await _draft(drafter, sar_input)
        second = await _draft(drafter, sar_input)
    finally:
        await endpoint.close()

    assert first.status is SarDraftStatus.DRAFT
    assert first.cached is False
    assert second.cached is True
    assert endpoint.requests == 1


@pytest.mark.asyncio
async def test_a_tightened_quality_policy_invalidates_every_cached_draft(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A cached pre-change draft must not replay past the policy now in force."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(gate_passing_json(sar_input))
    cache = InMemorySarDraftCache()

    try:
        await _draft(endpoint.drafter(cache=cache), sar_input)
        replayed = await _draft(endpoint.drafter(cache=cache, gate=_tightened_gate()), sar_input)
    finally:
        await endpoint.close()

    assert replayed.cached is False
    assert endpoint.requests == 2


@pytest.mark.asyncio
async def test_a_changed_prompt_version_invalidates_every_cached_draft(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A draft written by the retired prompt is not an answer from the shipped one."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(gate_passing_json(sar_input))
    cache = InMemorySarDraftCache()

    try:
        await _draft(endpoint.drafter(cache=cache), sar_input)
        replayed = await _draft(
            endpoint.drafter(cache=cache, prompt=SarPromptTemplate.load("v1")), sar_input
        )
    finally:
        await endpoint.close()

    assert replayed.cached is False
    assert endpoint.requests == 2


@pytest.mark.asyncio
async def test_a_changed_cascade_profile_invalidates_every_cached_draft(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A different stage on a different endpoint is a different draft, not a replay of one."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(gate_passing_json(sar_input))
    cache = InMemorySarDraftCache()

    try:
        await _draft(endpoint.drafter(cache=cache), sar_input)
        replayed = await _draft(endpoint.drafter(cache=cache, profile=_OTHER_PROFILE), sar_input)
    finally:
        await endpoint.close()

    assert replayed.cached is False
    assert endpoint.requests == 2


@pytest.mark.asyncio
async def test_a_gate_rejected_draft_is_never_cached_for_replay(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """Only accepted drafts enter the cache, so no replay can ever bypass the gate."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(fabricated_citation_json(sar_input))
    cache = InMemorySarDraftCache()

    try:
        drafter = endpoint.drafter(cache=cache)
        first = await _draft(drafter, sar_input)
        second = await _draft(drafter, sar_input)
    finally:
        await endpoint.close()

    assert first.status is SarDraftStatus.FAILED
    assert first.quality is not None
    assert SarGateReason.CITATION_FABRICATED in first.quality.reasons
    assert second.cached is False
    assert endpoint.requests == 2


class _RejectedAgentWorkflow:
    """A multi-agent primary whose reviewed output the production gate refuses."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls = 0

    async def draft(self, _sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Emit the terminal gate failure the real graph produces for a fabricated id."""
        self.calls += 1
        yield SarStreamEvent(
            type=SarEventType.FAILED,
            result=SarDraftResult(
                status=SarDraftStatus.FAILED,
                model_id="openrouter/writer",
                prompt_version="agent-v1",
                prompt_hash="hash",
                error_code="sar_quality_gate_failed",
                workflow="multi_agent",
                quality=production_gate().rejected(SarGateReason.CITATION_FABRICATED),
            ),
        )


@pytest.mark.asyncio
async def test_a_gate_rejected_agent_draft_falls_back_to_the_single_writer_cascade(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """The agent workflow is held to the same gate, and a refusal is handed to the cascade."""
    sar_input = make_sar_input()
    endpoint = _Endpoint(gate_passing_json(sar_input))
    primary = _RejectedAgentWorkflow()

    try:
        drafter = LiveAgentFallbackDrafter(
            primary=primary,
            fallback=endpoint.drafter(cache=InMemorySarDraftCache()),
        )
        events = [event async for event in drafter.draft(sar_input)]
    finally:
        await endpoint.close()

    result = events[-1].result
    assert primary.calls == 1
    assert endpoint.requests == 1
    assert result.status is SarDraftStatus.DRAFT
    assert result.workflow != "multi_agent"
    assert result.quality is not None and result.quality.passed
    assert not any(event.type is SarEventType.FAILED for event in events)
