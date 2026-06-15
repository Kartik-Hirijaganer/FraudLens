"""Summary: The SAR spend budget guard + token-cost estimation (plan §7.6, §16 Phase 7).
`estimate_cost_usd` prices a call from the catalog model card's verified per-million-token pricing
and the normalized usage, returning a `Decimal` that matches the `sar_drafts.cost_usd`
`NUMERIC(12,6)` column (no float drift). `BudgetGuard` enforces the per-session and per-day USD
caps that keep the LLM bill bounded: the live drafter calls `ensure_within_budget` BEFORE each
provider call and raises `SarBudgetExceededError` (the caller maps it to HTTP 429, plan §5.4/§7.6)
when a limit is already met, then `record`s the spend so the running session total is enforced
across calls. Limits AND the day's prior spend are injected, never hardcoded: the guard owns the
combined daily check by calling an injected `daily_spent_provider`, so a later phase can wire the
`system_config` caps + a `sar_drafts` day-sum query (plan §5.4/§7.6) without touching the drafter.

Key classes:
- SarBudgetExceededError: raised when a SAR call would exceed the session or daily USD budget.
- BudgetGuard: tracks in-session spend and enforces the session + daily USD caps.

Key functions:
- estimate_cost_usd: estimate a call's USD cost from catalog pricing + token usage.

Notes:
- A `None` limit means "uncapped" for that dimension; the daily check combines the injected
  spend-so-far-today (from `daily_spent_provider`, or 0 when unset) with this session's running
  total, so neither budget can be straddled and the drafter never needs DB access itself.
- Cost is computed in `Decimal` (quantized to 6 dp) so persisted spend reconciles exactly with the
  column scale; non-token pricing or missing prices yield `Decimal("0")` rather than a guess.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from fraudlens_llm import LlmUsage, ModelCard

_TOKENS_PER_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.000001")


class SarBudgetExceededError(RuntimeError):
    """Raised when a SAR draft would exceed the per-session or per-day USD budget."""


def estimate_cost_usd(card: ModelCard, usage: LlmUsage) -> Decimal:
    """Estimate a call's USD cost from the card's token pricing; Decimal('0') when unpriced."""
    if card.pricing_basis != "per_million_tokens":
        return Decimal("0")
    input_price = card.input_price_per_million
    output_price = card.output_price_per_million
    if input_price is None and output_price is None:
        return Decimal("0")
    input_cost = Decimal(usage.input_tokens) * Decimal(str(input_price or 0)) / _TOKENS_PER_MILLION
    output_cost = (
        Decimal(usage.output_tokens) * Decimal(str(output_price or 0)) / _TOKENS_PER_MILLION
    )
    return (input_cost + output_cost).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


class BudgetGuard:
    """Tracks in-session SAR spend and enforces the per-session + per-day USD caps."""

    def __init__(
        self,
        *,
        session_limit_usd: Decimal | None = None,
        daily_limit_usd: Decimal | None = None,
        daily_spent_provider: Callable[[], Decimal] | None = None,
    ) -> None:
        """Bind the session/daily USD caps + the day's prior-spend provider (None = uncapped)."""
        self._session_limit = session_limit_usd
        self._daily_limit = daily_limit_usd
        self._daily_spent_provider = daily_spent_provider
        self._session_spent = Decimal("0")

    @property
    def session_spent_usd(self) -> Decimal:
        """The USD spent during this session so far."""
        return self._session_spent

    def ensure_within_budget(self) -> None:
        """Raise SarBudgetExceededError when the session or combined daily cap is already met."""
        if self._session_limit is not None and self._session_spent >= self._session_limit:
            raise SarBudgetExceededError("SAR session budget exceeded")
        if self._daily_limit is None:
            return
        daily_spent = self._daily_spent_provider() if self._daily_spent_provider else Decimal("0")
        if daily_spent + self._session_spent >= self._daily_limit:
            raise SarBudgetExceededError("SAR daily budget exceeded")

    def record(self, cost_usd: Decimal) -> None:
        """Add a completed call's cost to the running session total."""
        self._session_spent += cost_usd
