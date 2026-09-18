"""Summary: CI integration coverage for the real vLLM SAR route over an in-memory transport.

Key classes:
- (none)

Key functions:
- test_vllm_sar_route_streams_grounded_zero_cost_result: Exercise config through transport.
- test_a_vllm_503_on_tier_one_escalates_to_the_bf16_endpoint: serving failure escalates.
- test_a_stopped_bf16_endpoint_fails_without_looping_back_to_awq: exhaustion is bounded.
- test_readiness_revalidates_the_hosted_route_upstream_eligibility: ZDR route is re-derived.
- test_unreadable_route_metadata_fails_readiness_closed: an unparseable answer is not consent.

Notes:
- The OpenAI-compatible endpoint is an httpx MockTransport; no socket or GPU is required.
- The cascade profile's two self-hosted stages must resolve to two DIFFERENT transports. One
  `vllm` governance entry with one base URL would collapse them onto one endpoint, and the
  escalation the release is named after could not happen at all (AD-2.5).
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint
from sar_drafts import gate_passing_json

from fraudlens_backend.api import ops
from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_gated import QualityGatedSarDrafter
from fraudlens_backend.sar.factory import build_sar_drafter, load_sar_llm_config
from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir
from fraudlens_llm import LlmClient, LlmSettings, load_catalog, load_providers
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_ml.sar import SarDraftStatus, SarEventType

_PROFILE = "awq-bf16"


def _settings() -> LlmSettings:
    config_dir = find_config_dir()
    return LlmSettings(
        catalog_path=config_dir / "llm" / "catalog.yml",
        providers_path=config_dir / "llm" / "providers.yml",
        environment="dev",
    )


def _bind_transport(
    client: LlmClient, model: str, connection: str, endpoint: CapturedOpenAiEndpoint
) -> httpx.AsyncClient:
    """Point one named connection's adapter at an in-memory transport."""
    adapter = client._adapter_for(client._resolve_model(model, connection))
    assert isinstance(adapter, OpenAiCompatibleAdapter)
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    adapter._client = AsyncOpenAI(
        api_key="synthetic-test-value",
        base_url=f"http://{connection}.test/v1",
        http_client=transport_client,
        max_retries=0,
    )
    return transport_client


def test_cascade_stages_resolve_to_distinct_connections() -> None:
    """Two tiers, one governance entry, two runtime-injected endpoints."""
    providers = load_providers(_settings().providers_path)
    stages = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages(_PROFILE)

    routes = [providers.route(stage.model.partition("/")[0], stage.connection) for stage in stages]

    assert [stage.connection for stage in stages] == ["runpod-awq", "runpod-bf16"]
    assert [route.base_url_env for route in routes] == ["VLLM_AWQ_BASE_URL", "VLLM_BF16_BASE_URL"]
    assert [route.api_key_env for route in routes] == ["VLLM_AWQ_API_KEY", "VLLM_BF16_API_KEY"]
    assert all(route.zdr_supported and route.data_retention == "none" for route in routes)


@pytest.mark.asyncio
async def test_vllm_sar_route_streams_grounded_zero_cost_result(make_sar_input) -> None:
    """The selected vLLM cascade reaches the real adapter with the governed payload."""
    settings = _settings()
    catalog = load_catalog(settings.catalog_path)
    providers = load_providers(settings.providers_path)
    sar_config = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml"))
    stages = sar_config.stages(_PROFILE)
    client = LlmClient.from_config(catalog, providers, settings)

    sar_input = make_sar_input()
    endpoint = CapturedOpenAiEndpoint(gate_passing_json(sar_input))
    bf16_endpoint = CapturedOpenAiEndpoint(gate_passing_json(sar_input))
    transports = [
        _bind_transport(client, stages[0].model, "runpod-awq", endpoint),
        _bind_transport(client, stages[1].model, "runpod-bf16", bf16_endpoint),
    ]
    drafter = build_sar_drafter(
        AppSettings(
            environment="dev",
            llm_mode="live",
            sar_config_file="llm/sar-vllm.yml",
            sar_profile=_PROFILE,
        ),
        client=client,
        catalog=catalog,
        budget=BudgetGuard(),
        cache=InMemorySarDraftCache(),
    )
    assert isinstance(drafter, QualityGatedSarDrafter)

    try:
        events = [event async for event in drafter.draft(sar_input)]
    finally:
        for transport in transports:
            await transport.aclose()

    result = events[-1].result
    assert result is not None
    assert events[-1].type == SarEventType.COMPLETED
    assert result.status == SarDraftStatus.DRAFT
    assert result.provider == "vllm"
    assert result.model_id == stages[0].model
    assert result.cost_usd == Decimal("0")
    assert result.escalation_tier == 0  # tier 1 passed, so BF16 was never asked
    assert bf16_endpoint.request_bodies == []
    assert result.structured is not None
    assert result.structured.cited_regulations == ("31 CFR 1010.314",)

    assert len(endpoint.request_bodies) == 1
    payload = json.loads(endpoint.request_bodies[0])
    assert payload["model"] == "Qwen/Qwen2.5-7B-Instruct-AWQ"
    assert payload["stream"] is True
    assert payload["response_format"]["type"] == "json_schema"
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["citationIds"]["items"]["enum"] == ["31 CFR 1010.314"]
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "system",
        "user",
    ]


@pytest.mark.asyncio
async def test_a_vllm_503_on_tier_one_escalates_to_the_bf16_endpoint(make_sar_input) -> None:
    """A serving failure on the AWQ endpoint is escalated, not retried on the same GPU."""
    sar_input = make_sar_input()
    awq = CapturedOpenAiEndpoint(gate_passing_json(sar_input), failures=1)
    bf16 = CapturedOpenAiEndpoint(gate_passing_json(sar_input))

    events = await _run_cascade(sar_input, awq, bf16)
    result = events[-1].result

    assert result.status == SarDraftStatus.DRAFT
    assert result.escalation_tier == 1
    assert result.escalated_from == ("awq",)
    # The self-hosted profile carries no transport fallback, so the 503 ends tier 1 outright
    # instead of the client silently model-hopping off the GPU being benchmarked (AD-2.3).
    assert len(awq.request_bodies) == 1
    assert len(bf16.request_bodies) == 1
    assert [event.stage.stage for event in events if event.type is SarEventType.ESCALATED] == [
        "bf16"
    ]


@pytest.mark.asyncio
async def test_a_stopped_bf16_endpoint_fails_without_looping_back_to_awq(make_sar_input) -> None:
    """Both endpoints down is a bounded, explicit failure — never a second pass over tier 1."""
    sar_input = make_sar_input()
    awq = CapturedOpenAiEndpoint(gate_passing_json(sar_input), failures=1)
    bf16 = CapturedOpenAiEndpoint(gate_passing_json(sar_input), failures=1)

    events = await _run_cascade(sar_input, awq, bf16)
    result = events[-1].result

    assert result.status == SarDraftStatus.FAILED
    assert result.escalated_from == ("awq", "bf16")
    assert len(awq.request_bodies) == 1
    assert len(bf16.request_bodies) == 1
    assert events[-2].type == SarEventType.CASCADE_FAILED
    assert [attempt.stage for attempt in result.attempts] == ["awq", "bf16"]


@pytest.mark.asyncio
async def test_readiness_revalidates_the_hosted_route_upstream_eligibility(monkeypatch) -> None:
    """Which upstreams may serve a hosted model is dynamic, so readiness re-derives it."""
    providers = load_providers(_settings().providers_path)
    stage = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages("deployed-openrouter")[
        0
    ]
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(ops, "_fetch_status", _answering_endpoint())

    monkeypatch.setattr(ops, "_fetch_json", _route_metadata("OpenAI"))
    await ops._probe_stage(stage, providers, _settings(), timeout=1.0)

    monkeypatch.setattr(ops, "_fetch_json", _route_metadata("Some Unvetted Host"))
    with pytest.raises(ValueError, match="permitted upstream"):
        await ops._probe_stage(stage, providers, _settings(), timeout=1.0)


def _answering_endpoint():
    """Return a `_fetch_status` stand-in for an endpoint that answers."""

    async def _status(*_args: object, **_kwargs: object) -> int:
        return 200

    return _status


def _route_metadata(upstream: str):
    """Return a `_fetch_json` stand-in serving one upstream's live route metadata."""

    async def _json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"data": {"endpoints": [{"provider_name": upstream}]}}

    return _json


async def _run_cascade(
    sar_input, awq: CapturedOpenAiEndpoint, bf16: CapturedOpenAiEndpoint
) -> list:
    """Drive the committed two-stage cascade over two independent in-memory endpoints."""
    settings = _settings()
    catalog = load_catalog(settings.catalog_path)
    providers = load_providers(settings.providers_path)
    stages = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages(_PROFILE)
    client = LlmClient.from_config(catalog, providers, settings)
    transports = [
        _bind_transport(client, stages[0].model, "runpod-awq", awq),
        _bind_transport(client, stages[1].model, "runpod-bf16", bf16),
    ]
    drafter = build_sar_drafter(
        AppSettings(
            environment="dev",
            llm_mode="live",
            sar_config_file="llm/sar-vllm.yml",
            sar_profile=_PROFILE,
        ),
        client=client,
        catalog=catalog,
        budget=BudgetGuard(),
        cache=InMemorySarDraftCache(),
    )
    try:
        return [event async for event in drafter.draft(sar_input)]
    finally:
        for transport in transports:
            await transport.aclose()


@pytest.mark.asyncio
async def test_unreadable_route_metadata_fails_readiness_closed(monkeypatch) -> None:
    """An answer we cannot read is not evidence of an eligible route; readiness fails closed."""
    providers = load_providers(_settings().providers_path)
    stage = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages("deployed-openrouter")[
        0
    ]
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-value")
    monkeypatch.setattr(ops, "_fetch_status", _answering_endpoint())

    for payload in ({"data": {}}, {"data": {"endpoints": "not-a-list"}}, {}):
        monkeypatch.setattr(ops, "_fetch_json", _fixed_json(payload))
        with pytest.raises(ValueError, match="unreadable"):
            await ops._probe_stage(stage, providers, _settings(), timeout=1.0)


@pytest.mark.asyncio
async def test_route_metadata_is_decoded_as_an_object_or_refused(monkeypatch) -> None:
    """`_fetch_json` returns a decoded object and refuses anything that is not one."""
    monkeypatch.setattr(ops.url_request, "urlopen", _urlopen(b'{"data": {"endpoints": []}}'))
    assert await ops._fetch_json("http://route.test/v1/models", 1.0) == {"data": {"endpoints": []}}

    monkeypatch.setattr(ops.url_request, "urlopen", _urlopen(b"[1, 2, 3]"))
    with pytest.raises(ValueError, match="not an object"):
        await ops._fetch_json("http://route.test/v1/models", 1.0)


def _fixed_json(payload: dict):
    """Return a `_fetch_json` stand-in serving one fixed payload."""

    async def _json(*_args: object, **_kwargs: object) -> dict:
        return payload

    return _json


def _urlopen(body: bytes):
    """Return a `urlopen` stand-in yielding one fixed response body."""

    class _Response:
        status = 200

        def read(self) -> bytes:
            return body

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def _open(*_args: object, **_kwargs: object) -> _Response:
        return _Response()

    return _open
