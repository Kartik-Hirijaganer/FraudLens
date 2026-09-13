"""Summary: CI integration coverage for the real vLLM SAR route over an in-memory transport.

Key classes:
- (none)

Key functions:
- test_vllm_sar_route_streams_grounded_zero_cost_result: Exercise config through transport.

Notes:
- The OpenAI-compatible endpoint is an httpx MockTransport; no socket or GPU is required.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.factory import build_sar_drafter, load_sar_llm_config
from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir
from fraudlens_llm import LlmClient, LlmSettings, load_catalog, load_providers
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_ml.sar import SarDraftStatus, SarEventType

_SAR_RESPONSE = (
    '{"subject":"Synthetic structuring review","narrative":"Grounded narrative.",'
    '"sections":[{"heading":"Summary","body":"Review the synthetic activity."}],'
    '"citedRegulations":["31 CFR 1010.314","99 FAKE 1"],'
    '"recommendedAction":"Escalate for review"}'
)


@pytest.mark.asyncio
async def test_vllm_sar_route_streams_grounded_zero_cost_result(make_sar_input) -> None:
    """The selected vLLM profile reaches the real adapter with the governed payload."""
    config_dir = find_config_dir()
    settings = LlmSettings(
        catalog_path=config_dir / "llm" / "catalog.yml",
        providers_path=config_dir / "llm" / "providers.yml",
        environment="dev",
    )
    catalog = load_catalog(settings.catalog_path)
    providers = load_providers(settings.providers_path)
    sar_config = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml"))
    client = LlmClient.from_config(catalog, providers, settings)
    resolved = client._resolve_model(sar_config.model)
    adapter = client._adapter_for(resolved)
    assert isinstance(adapter, OpenAiCompatibleAdapter)

    endpoint = CapturedOpenAiEndpoint(_SAR_RESPONSE)
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    adapter._client = AsyncOpenAI(
        api_key="synthetic-test-value",
        base_url="http://vllm.test/v1",
        http_client=transport_client,
        max_retries=0,
    )
    drafter = build_sar_drafter(
        AppSettings(environment="dev", llm_mode="live", sar_config_file="llm/sar-vllm.yml"),
        client=client,
        catalog=catalog,
        budget=BudgetGuard(),
        cache=InMemorySarDraftCache(),
    )
    assert isinstance(drafter, LiveSarDrafter)
    assert drafter._fallbacks == ()

    try:
        events = [event async for event in drafter.draft(make_sar_input())]
    finally:
        await transport_client.aclose()

    result = events[-1].result
    assert result is not None
    assert events[-1].type == SarEventType.COMPLETED
    assert result.status == SarDraftStatus.DRAFT
    assert result.provider == "vllm"
    assert result.model_id == sar_config.model
    assert result.cost_usd == Decimal("0")
    assert result.structured is not None
    assert result.structured.cited_regulations == ("31 CFR 1010.314",)

    assert len(endpoint.request_bodies) == 1
    payload = json.loads(endpoint.request_bodies[0])
    assert payload["model"] == "Qwen/Qwen2.5-7B-Instruct-AWQ"
    assert payload["stream"] is True
    assert payload["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "system",
        "user",
    ]
