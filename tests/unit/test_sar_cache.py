"""Unit tests for the SAR draft replay cache + its deterministic fingerprint (plan §7.6)."""

from __future__ import annotations

from decimal import Decimal

from fraudlens_backend.sar.cache import InMemorySarDraftCache, sar_cache_key
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus


def _result() -> SarDraftResult:
    return SarDraftResult(
        status=SarDraftStatus.DRAFT,
        content="# SAR",
        model_id="mock",
        prompt_version="v1@1.0.0",
        prompt_hash="hash",
        cost_usd=Decimal("0"),
    )


def test_cache_key_is_deterministic(make_sar_input) -> None:
    sar_input = make_sar_input()
    assert sar_cache_key("m", "h", sar_input) == sar_cache_key("m", "h", sar_input)


def test_cache_key_varies_with_model_prompt_and_input(make_sar_input) -> None:
    sar_input = make_sar_input()
    base = sar_cache_key("m", "h", sar_input)
    assert sar_cache_key("other", "h", sar_input) != base
    assert sar_cache_key("m", "other", sar_input) != base
    assert sar_cache_key("m", "h", make_sar_input(fraud_probability=0.1)) != base


def test_in_memory_cache_miss_then_hit() -> None:
    cache = InMemorySarDraftCache()
    assert cache.get("k") is None
    result = _result()
    cache.set("k", result)
    assert cache.get("k") is result
