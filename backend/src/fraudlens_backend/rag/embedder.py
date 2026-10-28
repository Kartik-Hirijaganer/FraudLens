"""Summary: Synchronous RAG embedder backed by the guardrailed async `LlmClient`. It adapts
`LlmClient.embed()` to fraudlens-ml's synchronous `Embedder` protocol without importing the LLM
package into fraudlens-ml. Calls run on one lazily started background event-loop thread, so query
embedding remains safe when the synchronous retriever is invoked from an active asyncio loop.

Key classes:
- LlmClientEmbedder: async-to-sync adapter with declared embedding-space provenance.

Key functions:
- (none)

Notes:
- Inputs flow through `LlmClient`, preserving PHI masking, data-class policy, safe usage logging,
  and the OpenRouter-only provider configuration.
- Provider output count and dimensions are validated before vectors reach ChromaDB.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections import OrderedDict
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from fraudlens_llm import DataClass, LlmClient, get_llm_settings, load_catalog, load_providers
from fraudlens_ml.rag import EmbeddingProvenance

if TYPE_CHECKING:
    from fraudlens_backend.rag.budget import QueryEmbeddingBudget


class LlmClientEmbedder:
    """Bridge the async guardrailed LLM embedding client to the synchronous RAG protocol."""

    def __init__(  # noqa: PLR0913 - explicit model provenance and bounded query policy.
        self,
        *,
        client: LlmClient,
        model: str,
        dimensions: int,
        rag_version: str,
        data_class: DataClass,
        query_cache_size: int = 256,
        max_query_chars: int = 2000,
        query_budget: QueryEmbeddingBudget | None = None,
    ) -> None:
        """Bind the client, configured embedding space, and provider data classification."""
        self._client = client
        self._model = model
        self._data_class = data_class
        self._provenance = EmbeddingProvenance(
            kind="provider",
            model_id=model,
            dimensions=dimensions,
            rag_version=rag_version,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._state_lock = threading.Lock()
        self._closed = False
        self._query_cache_size = query_cache_size
        self._max_query_chars = max_query_chars
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._query_lock = threading.Lock()
        self.input_tokens = 0
        self.cost_usd = Decimal("0")
        _provider, _model, self._card = load_catalog(get_llm_settings().catalog_path).get(model)
        provider = load_providers(get_llm_settings().providers_path).providers[_provider]
        self._attempts = provider.max_retries + 1
        self._query_budget = query_budget

    @property
    def provenance(self) -> EmbeddingProvenance:
        """Return the provider embedding-space identity persisted on the RAG index."""
        return self._provenance

    def _run_loop(self) -> None:
        """Own and run the adapter's dedicated asyncio loop until `close` stops it."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start the background event loop once and return it after initialization."""
        with self._state_lock:
            if self._closed:
                raise RuntimeError("embedder is closed")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="fraudlens-rag-embedder",
                    daemon=True,
                )
                self._thread.start()
        self._loop_ready.wait()
        if self._loop is None:  # defensive: the ready event is set only after assignment
            raise RuntimeError("embedding event loop failed to start")
        return self._loop

    async def _aembed(self, inputs: Sequence[str]) -> list[list[float]]:
        """Call the guardrailed client and validate response shape against provenance."""
        result = await self._client.embed(
            inputs,
            model=self._model,
            data_class=self._data_class,
        )
        rows = result.embeddings
        if len(rows) != len(inputs) or any(len(row) != self._provenance.dimensions for row in rows):
            raise ValueError("provider returned vectors inconsistent with configured provenance")
        from fraudlens_backend.sar.budget import estimate_cost_usd  # noqa: PLC0415

        self.input_tokens += result.usage.input_tokens
        self.cost_usd += estimate_cost_usd(self._card, result.usage)
        return rows

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a document batch through the background event loop."""
        if not texts:
            return []
        future = asyncio.run_coroutine_threadsafe(self._aembed(texts), self._ensure_loop())
        return future.result()

    def embed_query(self, text: str) -> list[float]:
        """Bound query size and cache by digest; repeated calls do not spend provider tokens."""
        if len(text) > self._max_query_chars:
            raise ValueError("embedding query exceeds configured character limit")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._query_lock:
            if self._closed:
                raise RuntimeError("embedder is closed")
            if digest in self._query_cache:
                self._query_cache.move_to_end(digest)
                return list(self._query_cache[digest])
            if self._query_budget is not None:
                from fraudlens_backend.rag.budget import maximum_query_cost  # noqa: PLC0415

                price = self._card.input_price_per_million
                if price is None or price <= 0:
                    raise RuntimeError("query embedding price unavailable")
                cost = maximum_query_cost(text, input_price=price, attempts=self._attempts)
                admission = asyncio.run_coroutine_threadsafe(
                    self._query_budget.reserve(cost), self._ensure_loop()
                )
                admission.result()
            row = self.embed_documents([text])[0]
            if self._query_cache_size:
                self._query_cache[digest] = list(row)
                if len(self._query_cache) > self._query_cache_size:
                    self._query_cache.popitem(last=False)
            return row

    def close(self) -> None:
        """Stop the background event loop after in-flight synchronous calls have completed."""
        with self._query_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                loop = self._loop
                thread = self._thread
            if loop is not None:
                if self._query_budget is not None:
                    asyncio.run_coroutine_threadsafe(self._query_budget.close(), loop).result()
                loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join()
