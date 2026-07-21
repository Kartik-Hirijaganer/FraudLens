"""Summary: Opt-in OpenRouter E2E coverage for native-streamed live SAR drafting.

Key classes:
- _FailingStreamingAdapter: Forced provider failure used to verify terminal degradation.

Key functions:
- test_openrouter_live_sar_drafter_streams_grounded_auditable_result: Exercise the real provider.

Notes:
- The registered llm_live marker is excluded by default; run explicitly through Infisical /llm.
- Inputs are synthetic and PHI-free. No provider response text is logged by the test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

import pytest

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.factory import SarLlmConfig, load_sar_llm_config
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_llm import (
    Catalog,
    GenerationParams,
    LlmClient,
    LlmMessage,
    LlmTimeoutError,
    ModelCard,
    TaskType,
    get_llm_settings,
    load_catalog,
    load_providers,
)
from fraudlens_llm.adapters.base import AdapterGenerateChunk
from fraudlens_ml.sar import SarDraftStatus, SarEventType

pytestmark = pytest.mark.llm_live


class _FailingStreamingAdapter:
    """Streaming adapter that deterministically raises a retryable provider timeout."""

    async def generate_stream(
        self,
        *,
        model_id: str,
        card: ModelCard,
        messages: Sequence[LlmMessage],
        params: GenerationParams,
    ) -> AsyncIterator[AdapterGenerateChunk]:
        _ = (card, messages, params)
        if model_id:
            raise LlmTimeoutError()
        yield AdapterGenerateChunk()


def _live_drafter(client: LlmClient, catalog: Catalog, config: SarLlmConfig) -> LiveSarDrafter:
    """Build the production live drafter with isolated budget and cache state."""
    return LiveSarDrafter(
        client=client,
        catalog=catalog,
        prompt=SarPromptTemplate.load(),
        model=config.model,
        max_output_tokens=config.max_output_tokens,
        reasoning_effort=config.reasoning_effort,
        budget=BudgetGuard(),
        cache=InMemorySarDraftCache(),
        fallbacks=config.fallbacks,
        task_type=TaskType.ANALYSIS,
    )


@pytest.mark.asyncio
async def test_openrouter_live_sar_drafter_streams_grounded_auditable_result(
    make_sar_input,
) -> None:
    settings = get_llm_settings()
    config = load_sar_llm_config()
    provider_name = config.model.partition("/")[0]
    providers = load_providers(settings.providers_path)
    api_key_env = providers.get(provider_name).api_key_env
    if not os.getenv(api_key_env):
        pytest.skip(f"{api_key_env} is required for the opt-in llm_live test")

    catalog = load_catalog(settings.catalog_path)
    client = LlmClient.from_config(catalog, providers, settings)
    sar_input = make_sar_input()
    available_citations = {citation.citation for citation in sar_input.citations}

    events = [event async for event in _live_drafter(client, catalog, config).draft(sar_input)]
    result = events[-1].result

    assert result is not None
    assert events[-1].type == SarEventType.COMPLETED, result.error_code
    assert result.status == SarDraftStatus.DRAFT
    assert result.structured is not None
    assert set(result.structured.cited_regulations) <= available_citations
    assert {citation.citation for citation in result.citations} <= available_citations
    assert tuple(citation.citation for citation in result.citations) == (
        result.structured.cited_regulations
    )
    assert result.token_usage.input_tokens > 0
    assert result.token_usage.output_tokens > 0
    assert result.token_usage.total_tokens > 0
    assert result.cost_usd > Decimal("0")
    assert result.prompt_version
    assert result.prompt_hash
    assert result.provider == provider_name

    client._adapters[provider_name] = _FailingStreamingAdapter()
    failed_events = [
        event async for event in _live_drafter(client, catalog, config).draft(sar_input)
    ]
    failed = failed_events[-1].result

    assert failed_events[-1].type == SarEventType.FAILED
    assert failed is not None
    assert failed.status == SarDraftStatus.FAILED
    assert failed.error_code == "llm_timeout"
