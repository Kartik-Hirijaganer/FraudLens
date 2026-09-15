"""Unit tests for the SAR draft replay cache + its deterministic fingerprint (plan §7.6)."""

from __future__ import annotations

from decimal import Decimal

from fraudlens_backend.sar.cache import (
    InMemorySarDraftCache,
    SarCacheGenerationSettings,
    sar_cache_key,
)
from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
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


def _key(
    sar_input,
    *,
    model: str = "m",
    prompt: str = "h",
    agency: str | None = None,
    max_output_tokens: int = 256,
) -> str:
    return sar_cache_key(
        model_id=model,
        prompt_hash=prompt,
        agency_id=agency or sar_input.agency_id,
        model_input=project_for_model(sar_input, load_egress_policy()),
        generation_settings=SarCacheGenerationSettings(
            max_output_tokens=max_output_tokens,
            task_type="analysis",
        ),
    )


def test_cache_key_is_deterministic(make_sar_input) -> None:
    sar_input = make_sar_input()
    assert _key(sar_input) == _key(sar_input)


def test_cache_key_varies_with_model_prompt_and_input(make_sar_input) -> None:
    sar_input = make_sar_input()
    base = _key(sar_input)
    assert _key(sar_input, model="other") != base
    assert _key(sar_input, prompt="other") != base
    assert _key(sar_input, agency="another-tenant") != base
    assert _key(sar_input, max_output_tokens=512) != base
    assert _key(make_sar_input(fraud_probability=0.1)) != base


def test_in_memory_cache_miss_then_hit() -> None:
    cache = InMemorySarDraftCache()
    assert cache.get("k") is None
    result = _result()
    cache.set("k", result)
    assert cache.get("k") is result
