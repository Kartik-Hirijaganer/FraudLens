"""Summary: Hermetic live-RAG embedder and factory tests. A fake OpenRouter adapter supplies
canned vectors through the real guardrailed `LlmClient`, proving the async-to-sync bridge works
inside a running event loop, batches document inputs, validates dimensions, and never uses network.

Key classes:
- _FakeEmbeddingAdapter: deterministic provider seam used by the real LLM client.

Key functions:
- (none)

Notes:
- The repository RAG config and catalog are validated together so the configured OpenRouter model
  reference, dimensions, and audit version cannot drift independently.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from fraudlens_backend.rag import (
    LlmClientEmbedder,
    RagEmbeddingConfig,
    build_embedder,
    load_rag_embedding_config,
)
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import (
    Catalog,
    DataClass,
    GenerationParams,
    Kind,
    Lifecycle,
    LlmClient,
    LlmSettings,
    LlmUsage,
    ModelCard,
    Protocol,
    ProviderConfig,
    Providers,
    load_catalog,
)
from fraudlens_llm.adapters.base import AdapterEmbeddingResult
from fraudlens_ml.rag import HashingEmbedder

_MODEL_REF = "openrouter/openai/text-embedding-3-small"
_MODEL_ID = "openai/text-embedding-3-small"
_DIMENSIONS = 3


class _FakeEmbeddingAdapter:
    """Return deterministic vectors while recording the provider batch inputs."""

    def __init__(self, *, dimensions: int = _DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(
        self,
        *,
        model_id: str,
        card: ModelCard,
        inputs: Sequence[str],
        params: GenerationParams,
    ) -> AdapterEmbeddingResult:
        self.calls.append(list(inputs))
        return AdapterEmbeddingResult(
            embeddings=[[float(index + 1)] * self.dimensions for index, _ in enumerate(inputs)],
            usage=LlmUsage(input_tokens=len(inputs), total_tokens=len(inputs)),
        )


def _card() -> ModelCard:
    return ModelCard(
        kind=Kind.EMBED,
        context_window=8192,
        default_params=GenerationParams(dimensions=_DIMENSIONS),
        input_price_per_million=0.02,
        output_price_per_million=0.02,
        lifecycle=Lifecycle.GA,
        callable=True,
        pricing_basis="per_million_tokens",
    )


def _provider() -> ProviderConfig:
    return ProviderConfig(
        protocol=Protocol.OPENAI_COMPATIBLE,
        base_url="https://provider.test/v1",
        api_key_env="PLACEHOLDER_API_KEY",
        timeout_s=10,
        max_retries=0,
        region="global",
        data_retention="provider-default",
        zdr_supported=False,
        training_opt_out=False,
        baa_required=False,
        allowed_data_classes=[DataClass.DEIDENTIFIED],
    )


def _client(adapter: _FakeEmbeddingAdapter) -> LlmClient:
    client = LlmClient.from_config(
        Catalog(providers={"openrouter": {_MODEL_ID: _card()}}),
        Providers(providers={"openrouter": _provider()}),
        LlmSettings(environment="dev", default_model=_MODEL_REF),
    )
    client._adapters["openrouter"] = adapter
    return client


def _config() -> RagEmbeddingConfig:
    return RagEmbeddingConfig(
        model=_MODEL_REF,
        dimensions=_DIMENSIONS,
        rag_version="rag-test-live",
        data_class=DataClass.DEIDENTIFIED,
    )


@pytest.mark.asyncio
async def test_live_embedder_bridges_running_loop_and_batches_documents() -> None:
    adapter = _FakeEmbeddingAdapter()
    embedder = LlmClientEmbedder(
        client=_client(adapter),
        model=_MODEL_REF,
        dimensions=_DIMENSIONS,
        rag_version="rag-test-live",
        data_class=DataClass.DEIDENTIFIED,
    )
    try:
        assert embedder.embed_documents(["first provision", "second provision"]) == [
            [1.0, 1.0, 1.0],
            [2.0, 2.0, 2.0],
        ]
        assert embedder.embed_query("breaking deposits below a threshold") == [1.0, 1.0, 1.0]
        assert adapter.calls == [
            ["first provision", "second provision"],
            ["breaking deposits below a threshold"],
        ]
        assert embedder.provenance.dimensions == _DIMENSIONS
    finally:
        embedder.close()


def test_live_embedder_rejects_provider_dimension_drift() -> None:
    embedder = LlmClientEmbedder(
        client=_client(_FakeEmbeddingAdapter(dimensions=_DIMENSIONS + 1)),
        model=_MODEL_REF,
        dimensions=_DIMENSIONS,
        rag_version="rag-test-live",
        data_class=DataClass.DEIDENTIFIED,
    )
    try:
        with pytest.raises(ValueError, match="configured provenance"):
            embedder.embed_query("query")
    finally:
        embedder.close()


def test_factory_selects_offline_and_live_embedders(make_settings) -> None:
    offline = build_embedder(make_settings(rag_embedding_mode="offline"))
    assert isinstance(offline, HashingEmbedder)
    assert offline.provenance.rag_version == "rag-v1"

    live = build_embedder(
        make_settings(rag_embedding_mode="live"),
        client=_client(_FakeEmbeddingAdapter()),
        config=_config(),
    )
    assert isinstance(live, LlmClientEmbedder)
    assert live.provenance.rag_version == "rag-test-live"
    live.close()


def test_repo_rag_config_resolves_to_matching_openrouter_catalog_entry() -> None:
    config = load_rag_embedding_config()
    catalog = load_catalog(find_config_dir() / "llm" / "catalog.yml")
    provider, model_id, card = catalog.get(config.model)
    assert provider == "openrouter"
    assert model_id == "openai/text-embedding-3-small"
    assert card.kind is Kind.EMBED
    assert card.default_params.dimensions == config.dimensions
    assert config.rag_version == "rag-v2-te3s"
