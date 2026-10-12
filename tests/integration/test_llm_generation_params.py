"""Summary: Offline generation-parameter precedence and serialized GPT-5 mini regressions.

Key classes:
- (none)

Key functions:
- test_parameter_precedence_preserves_omissions_and_explicit_null: verify inheritance and clearing.
- test_gpt5_mini_omits_temperature_at_http_boundary: exercise real catalog, client and SDK bytes.
- test_fallback_remerges_parameters_from_its_own_model_card: avoid leaking primary parameters.

Notes:
- Real OpenAI SDK serialization runs over httpx MockTransport without sockets or credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint

from fraudlens_llm import (
    GenerationParams,
    LlmClient,
    LlmMessage,
    LlmSettings,
    StreamGenerationRequest,
    load_catalog,
    load_providers,
)
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_llm.client_guardrails import merge_params

CONFIG = Path(__file__).resolve().parents[2] / "config" / "llm"


@pytest.mark.parametrize(
    ("card", "overrides", "temperature", "tokens"),
    [
        (GenerationParams(), None, 0.05, 3000),
        (GenerationParams(temperature=None), None, None, 3000),
        (GenerationParams(temperature=0.2), GenerationParams(temperature=None), None, 3000),
        (
            GenerationParams(temperature=None),
            GenerationParams(temperature=0.3, max_tokens=100),
            0.3,
            100,
        ),
        (GenerationParams(), GenerationParams(max_tokens=100), 0.05, 100),
    ],
)
def test_parameter_precedence_preserves_omissions_and_explicit_null(
    card: GenerationParams,
    overrides: GenerationParams | None,
    temperature: float | None,
    tokens: int,
) -> None:
    """An explicit null clears a lower layer while omitted fields retain its values."""
    defaults = GenerationParams(temperature=0.05, max_tokens=3000, response_format="json_object")

    merged = merge_params(defaults, card, overrides)

    assert merged.temperature == temperature
    assert merged.max_tokens == tokens
    assert merged.response_format == defaults.response_format
    assert ("temperature" in merged.model_dump(exclude_none=True)) is (temperature is not None)
    assert defaults.temperature == 0.05


def _client(endpoint: CapturedOpenAiEndpoint) -> tuple[LlmClient, AsyncOpenAI]:
    """Bind real config and provider adapters to one socket-free SDK transport."""
    settings = LlmSettings(
        environment="dev",
        catalog_path=CONFIG / "catalog.yml",
        providers_path=CONFIG / "providers.yml",
        default_params=GenerationParams(temperature=0.05, max_tokens=1024),
    )
    providers = load_providers(settings.providers_path)
    client = LlmClient.from_config(load_catalog(settings.catalog_path), providers, settings)
    sdk = AsyncOpenAI(
        api_key="offline-placeholder",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint)),
    )
    for provider in ("openai", "openrouter"):
        adapter = OpenAiCompatibleAdapter(provider, providers.providers[provider], settings)
        adapter._client = sdk
        client._adapters[provider] = adapter
    return client, sdk


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["openai/gpt-5-mini", "openrouter/openai/gpt-5-mini"])
async def test_gpt5_mini_omits_temperature_at_http_boundary(model: str) -> None:
    """Catalog null suppresses the global default in the actual serialized stream request."""
    endpoint = CapturedOpenAiEndpoint("Synthetic parameter verification passed.")
    client, sdk = _client(endpoint)
    try:
        result = await client.generate_stream(
            StreamGenerationRequest(
                model=model,
                messages=[LlmMessage(role="user", content="Check synthetic configuration.")],
                overrides=GenerationParams(
                    max_tokens=3000,
                    reasoning_effort="minimal",
                    response_format="json_object",
                ),
            )
        )
    finally:
        await sdk.close()

    assert result.safe_text == endpoint.response_text
    assert len(endpoint.request_bodies) == 1
    payload = json.loads(endpoint.request_bodies[0])
    assert "temperature" not in payload
    assert payload["max_tokens"] == 3000
    assert payload["reasoning_effort"] == "minimal"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_is_mini", [True, False])
async def test_fallback_remerges_parameters_from_its_own_model_card(
    primary_is_mini: bool,
) -> None:
    """A failed route cannot carry a temperature value or null into the fallback model."""
    endpoint = CapturedOpenAiEndpoint("Synthetic fallback verification passed.", failures=1)
    client, sdk = _client(endpoint)
    models = ["openrouter/openai/gpt-5-mini", "openrouter/google/gemini-2.5-flash"]
    if not primary_is_mini:
        models.reverse()
    try:
        result = await client.generate_stream(
            StreamGenerationRequest(
                model=models[0],
                fallbacks=(models[1],),
                messages=[LlmMessage(role="user", content="Check synthetic fallback.")],
                overrides=GenerationParams(max_tokens=3000),
            )
        )
    finally:
        await sdk.close()

    assert result.safe_text == endpoint.response_text
    assert len(endpoint.request_bodies) == len(models)
    for model, raw in zip(models, endpoint.request_bodies, strict=True):
        payload = json.loads(raw)
        _, _, card = client._catalog.get(model)
        assert payload.get("temperature") == card.default_params.temperature
        assert ("temperature" in payload) is (card.default_params.temperature is not None)
        assert payload["max_tokens"] == 3000
