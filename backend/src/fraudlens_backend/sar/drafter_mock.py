"""Summary: The keyless mock SAR drafter (plan §7.7, §16 Phase 7). `MockSarDrafter` implements the
injected `fraudlens_ml.sar.SarDrafter` protocol with NO provider, NO API keys, and NO cost — it is
what `make local-demo` (and the offline test suite) drafts with, so the full investigate → stream →
SAR UX works completely offline. It deterministically composes a schema-valid `SarDraftContent`
from the PHI-free `SarInput` (the rule indicators that fired, the top SHAP drivers, the risk band,
and the available regulations), so the same input always yields the same SAR. Citations are grounded
by construction — it only ever cites ids drawn from `SarInput.citations`, so the mock can never
emit a fabricated regulation id (plan §8.1). The rendered content is PHI-masked via the shared
schema renderer, then streamed through the shared token streamer, exactly like the live drafter.

Key classes:
- MockSarDrafter: deterministic, keyless SarDrafter for local-demo and offline tests.

Key functions:
- (none)

Notes:
- It records the real prompt template's `prompt_version`/`prompt_hash` (so the provenance trail is
  identical to live), but `model_id="mock"`, zero cost, and a word-count token estimate — there is
  no provider call to price.
- It never raises for content reasons: a mock draft always succeeds with `status=draft`; the failed
  path is exercised only by the live drafter (provider/guardrail/schema failures, plan §7.5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.schema import render_markdown
from fraudlens_backend.sar.streaming import stream_result
from fraudlens_ml.sar import (
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarInput,
    SarSection,
    SarStreamEvent,
    SarTokenUsage,
)

_MOCK_MODEL_ID = "mock"


class MockSarDrafter:
    """A deterministic, keyless SAR drafter for local-demo and offline tests."""

    def __init__(self, prompt: SarPromptTemplate) -> None:
        """Bind the prompt template whose version/hash the mock records for provenance parity."""
        self._prompt = prompt

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Compose a deterministic, grounded, schema-valid SAR and stream it (no keys, no cost)."""
        content = _compose_content(sar_input)
        rendered = render_markdown(content)
        result = SarDraftResult(
            status=SarDraftStatus.DRAFT,
            content=rendered,
            structured=content,
            citations=sar_input.citations,
            model_id=_MOCK_MODEL_ID,
            provider=None,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            token_usage=SarTokenUsage(
                output_tokens=len(rendered.split()), total_tokens=len(rendered.split())
            ),
            cost_usd=Decimal("0"),
        )
        async for event in stream_result(result):
            yield event


def _compose_content(sar_input: SarInput) -> SarDraftContent:
    """Deterministically build a grounded SarDraftContent from the PHI-free input."""
    rule_summary = (
        ", ".join(f"{hit.rule_type.value} ({hit.code})" for hit in sar_input.rule_hits)
        or "no deterministic rules"
    )
    driver_summary = (
        ", ".join(feature.feature for feature in sar_input.top_features) or "no model drivers"
    )
    probability_pct = f"{sar_input.fraud_probability * 100:.1f}%"
    narrative = (
        f"A {sar_input.channel} transaction of {sar_input.amount} {sar_input.currency} originating "
        f"in {sar_input.country} scored at a {probability_pct} fraud probability and assigned a "
        f"{sar_input.risk_band.value} risk band. The deterministic rules engine flagged: "
        f"{rule_summary}. The leading model risk drivers were: {driver_summary}. This activity is "
        "consistent with potential money-laundering indicators and warrants human review."
    )
    sections = (
        SarSection(
            heading="Activity summary",
            body=(
                f"Transaction of {sar_input.amount} {sar_input.currency} via {sar_input.channel} "
                f"({sar_input.country}); model fraud probability {probability_pct}, "
                f"risk band {sar_input.risk_band.value}."
            ),
        ),
        SarSection(
            heading="Risk indicators",
            body=f"Rules fired: {rule_summary}. Model drivers: {driver_summary}.",
        ),
        SarSection(
            heading="Regulatory basis",
            body=(
                "; ".join(f"{c.citation} — {c.title}" for c in sar_input.citations)
                or "No specific regulatory citation matched."
            ),
        ),
    )
    return SarDraftContent(
        subject=f"Suspected {sar_input.risk_band.value}-risk {sar_input.channel} activity",
        narrative=narrative,
        sections=sections,
        cited_regulations=tuple(citation.citation for citation in sar_input.citations),
        recommended_action="Escalate to a compliance reviewer for a SAR filing decision.",
    )
