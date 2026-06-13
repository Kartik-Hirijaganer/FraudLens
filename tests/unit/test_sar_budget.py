"""Unit tests for SAR token-cost estimation + the session/daily budget guard (plan §7.6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fraudlens_backend.sar.budget import BudgetGuard, SarBudgetExceededError, estimate_cost_usd
from fraudlens_llm import GenerationParams, Kind, Lifecycle, LlmUsage, ModelCard


def _card(*, basis: str | None = "per_million_tokens", in_price=1.0, out_price=2.0) -> ModelCard:
    return ModelCard(
        kind=Kind.CHAT,
        context_window=1000,
        default_params=GenerationParams(max_tokens=50),
        input_price_per_million=in_price,
        output_price_per_million=out_price,
        source_url="https://example.com",
        verified_at="2026-06-10",
        lifecycle=Lifecycle.GA,
        callable=True,
        pricing_basis=basis,
    )


def test_estimate_cost_from_token_pricing() -> None:
    cost = estimate_cost_usd(
        _card(), LlmUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    )
    # 1000*1/1e6 + 500*2/1e6 = 0.001 + 0.001 = 0.002
    assert cost == Decimal("0.002000")


def test_estimate_cost_zero_for_non_token_basis() -> None:
    assert estimate_cost_usd(_card(basis="per_minute"), LlmUsage(input_tokens=1000)) == Decimal("0")
    assert estimate_cost_usd(_card(basis=None), LlmUsage(input_tokens=1000)) == Decimal("0")


def test_estimate_cost_zero_when_prices_missing() -> None:
    card = _card(in_price=None, out_price=None)
    assert estimate_cost_usd(card, LlmUsage(input_tokens=1000, output_tokens=500)) == Decimal("0")


def test_estimate_cost_handles_partial_pricing() -> None:
    card = _card(in_price=None, out_price=2.0)
    assert estimate_cost_usd(card, LlmUsage(output_tokens=1_000_000)) == Decimal("2.000000")


def test_budget_uncapped_never_raises() -> None:
    guard = BudgetGuard()
    guard.record(Decimal("100"))
    guard.ensure_within_budget()  # no caps → no raise


def test_budget_session_limit_raises_after_spend() -> None:
    guard = BudgetGuard(session_limit_usd=Decimal("0.005"))
    guard.ensure_within_budget()  # nothing spent yet → ok
    guard.record(Decimal("0.005"))
    assert guard.session_spent_usd == Decimal("0.005")
    with pytest.raises(SarBudgetExceededError, match="session"):
        guard.ensure_within_budget()


def test_budget_daily_limit_combines_provider_spend_and_session_spend() -> None:
    # The injected provider supplies the day's prior spend (a later phase queries sar_drafts);
    # the guard combines it with this session's running total — the drafter passes nothing.
    guard = BudgetGuard(
        daily_limit_usd=Decimal("1.00"), daily_spent_provider=lambda: Decimal("0.70")
    )
    guard.record(Decimal("0.40"))
    with pytest.raises(SarBudgetExceededError, match="daily"):
        guard.ensure_within_budget()  # 0.70 (provider) + 0.40 (session) >= 1.00


def test_budget_daily_limit_without_provider_counts_session_only() -> None:
    # A daily cap with no provider treats prior-day spend as 0, so only session spend accrues.
    guard = BudgetGuard(daily_limit_usd=Decimal("1.00"))
    guard.record(Decimal("0.90"))
    guard.ensure_within_budget()  # 0 + 0.90 < 1.00 → ok
    guard.record(Decimal("0.20"))
    with pytest.raises(SarBudgetExceededError, match="daily"):
        guard.ensure_within_budget()  # 0 + 1.10 >= 1.00
