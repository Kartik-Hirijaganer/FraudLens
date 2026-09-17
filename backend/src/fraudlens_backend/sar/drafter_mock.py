"""Summary: The keyless mock SAR drafter (plan §7.7, §16 Phase 7). `MockSarDrafter` implements the
injected `fraudlens_ml.sar.SarDrafter` protocol with NO provider, NO API keys, and NO cost — it is
what `make local-demo` (and the offline test suite) drafts with, so the full investigate → stream →
SAR UX works completely offline. It deterministically composes a schema-valid `SarDraftContent`
from the EGRESS-PROJECTED input and its evidence catalog: claim-level evidence refs, machine-
readable asserted facts copied from the catalog, and one section per FinCEN narrative element.
Since release 0.5.0 it then runs the SAME production `SarQualityGate` over that draft and attaches
the verdict, because no `status=draft` may be persisted without a passing gate — a mock that simply
stamped `passed=true` would be exactly the convention the repository boundary exists to forbid.
Citations are grounded by construction (it only cites ids drawn from `SarInput.citations`) and the
rendered content is PHI-masked by the shared renderer, then streamed by the shared token streamer.

Key classes:
- MockSarDrafter: deterministic, keyless, gate-passing SarDrafter for local-demo and tests.

Key functions:
- (none)

Notes:
- It records the real prompt template's `prompt_version`/`prompt_hash` (so the provenance trail is
  identical to live), but `model_id="mock"`, zero cost, and a word-count token estimate.
- It now fails exactly where live fails: an egress-ineligible source, or a composed draft the
  deterministic gate rejects, yields a terminal `failed` result rather than an ungated draft.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from fraudlens_backend.sar.egress import (
    EgressBlockedError,
    EgressPolicy,
    SarModelInput,
    load_egress_policy,
    project_for_model,
)
from fraudlens_backend.sar.evidence import SarEvidenceCatalog, build_evidence_catalog
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.sar.quality_gate import (
    SarFincenElement,
    SarQualityGate,
    load_sar_gate_policy,
)
from fraudlens_backend.sar.schema import render_markdown
from fraudlens_backend.sar.streaming import stream_result
from fraudlens_ml.sar import (
    SarClaim,
    SarClaimFact,
    SarDraftContent,
    SarDraftResult,
    SarDraftStatus,
    SarInput,
    SarQualityGateResult,
    SarSection,
    SarStreamEvent,
    SarTokenUsage,
)

_MOCK_MODEL_ID = "mock"
_GATE_FAILED_CODE = "sar_quality_gate_failed"
_TRANSACTION_REFS = (
    "txn.amount",
    "txn.currency",
    "txn.country",
    "txn.channel",
    "txn.direction",
    "txn.occurredAt",
)
_RISK_REFS = ("risk.band", "risk.fraudProbability")
_UNKNOWN = "unknown"


class MockSarDrafter:
    """A deterministic, keyless, gate-passing SAR drafter for local-demo and offline tests."""

    def __init__(
        self,
        prompt: SarPromptTemplate,
        *,
        gate: SarQualityGate | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        """Bind the prompt template plus the gate and egress policy the live path also uses."""
        self._prompt = prompt
        self._egress_policy = egress_policy or load_egress_policy()
        self._gate = gate or SarQualityGate(load_sar_gate_policy())

    async def draft(self, sar_input: SarInput) -> AsyncIterator[SarStreamEvent]:
        """Compose a deterministic grounded SAR, gate it, and stream it (no keys, no cost)."""
        try:
            model_input = project_for_model(sar_input, self._egress_policy)
        except EgressBlockedError as exc:
            async for event in stream_result(self._failed(exc.code)):
                yield event
            return
        catalog = build_evidence_catalog(model_input)
        content = _compose_content(sar_input, model_input, catalog)
        verdict = self._gate.evaluate(content, available=sar_input.citations, catalog=catalog)
        if not verdict.passed:
            async for event in stream_result(self._failed(_GATE_FAILED_CODE, quality=verdict)):
                yield event
            return
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
            quality=verdict,
        )
        async for event in stream_result(result):
            yield event

    def _failed(
        self, error_code: str, *, quality: SarQualityGateResult | None = None
    ) -> SarDraftResult:
        """Build the terminal failure for an ineligible or gate-rejected mock draft."""
        return SarDraftResult(
            status=SarDraftStatus.FAILED,
            model_id=_MOCK_MODEL_ID,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            error_code=error_code,
            quality=quality,
        )


def _display(catalog: SarEvidenceCatalog, ref: str) -> str:
    """Return one catalog fact's rendered form, or the explicit unknown marker."""
    fact = catalog.get(ref)
    return fact.display if fact is not None else _UNKNOWN


def _facts(catalog: SarEvidenceCatalog, refs: tuple[str, ...]) -> tuple[SarClaimFact, ...]:
    """Assert every named catalog fact verbatim (the mock never restates a value)."""
    return tuple(
        SarClaimFact(ref=fact.ref, value=fact.value)
        for fact in (catalog.get(ref) for ref in refs)
        if fact is not None
    )


def _rule_refs(catalog: SarEvidenceCatalog) -> tuple[str, ...]:
    """Return every rule-derived catalog ref in stable catalog order."""
    return tuple(fact.ref for fact in catalog.facts if fact.ref.startswith("rule."))


def _compose_content(
    sar_input: SarInput, model_input: SarModelInput, catalog: SarEvidenceCatalog
) -> SarDraftContent:
    """Deterministically build a grounded, fact-asserting SarDraftContent from the catalog."""
    citation_ids = tuple(dict.fromkeys(citation.citation for citation in sar_input.citations))
    rule_refs = _rule_refs(catalog)
    rule_summary = (
        ", ".join(_display(catalog, ref) for ref in rule_refs if ref.endswith(".type"))
        or "no deterministic rules"
    )
    driver_summary = (
        ", ".join(feature.feature for feature in sar_input.top_features) or "no model drivers"
    )
    amount = _display(catalog, "txn.amount")
    probability = _display(catalog, "risk.fraudProbability")
    band = _display(catalog, "risk.band")
    channel = _display(catalog, "txn.channel")
    country = _display(catalog, "txn.country")
    narrative = (
        f"A {channel} transaction of {amount} originating in {country} scored at a {probability} "
        f"fraud probability and was assigned a {band} risk band. The deterministic rules engine "
        f"flagged: {rule_summary}. The leading model risk drivers were: {driver_summary}. This "
        "activity is consistent with potential money-laundering indicators and warrants human "
        "review."
    )
    claims = (
        SarClaim(
            statement=f"The subject moved {amount} via {channel} in {country}.",
            evidence_refs=_TRANSACTION_REFS,
            citation_ids=citation_ids,
            asserted_facts=_facts(catalog, _TRANSACTION_REFS),
        ),
        SarClaim(
            statement=f"The blended model assigned a {band} band at {probability} probability.",
            evidence_refs=_RISK_REFS,
            asserted_facts=_facts(catalog, _RISK_REFS),
        ),
    ) + (
        (
            SarClaim(
                statement=f"Deterministic controls fired: {rule_summary}.",
                evidence_refs=rule_refs,
                asserted_facts=_facts(catalog, rule_refs),
            ),
        )
        if rule_refs
        else ()
    )
    return SarDraftContent(
        subject=f"Suspected {band}-risk {channel} activity",
        narrative=narrative,
        claims=claims,
        sections=_fincen_sections(model_input, catalog, rule_summary, driver_summary),
        cited_regulations=citation_ids,
        recommended_action="Escalate to a compliance reviewer for a SAR filing decision.",
    )


def _fincen_sections(
    model_input: SarModelInput,
    catalog: SarEvidenceCatalog,
    rule_summary: str,
    driver_summary: str,
) -> tuple[SarSection, ...]:
    """Render one non-empty section per FinCEN narrative element, in the required order."""
    bodies = {
        SarFincenElement.WHO: (
            f"Case {model_input.case_alias}: subject {model_input.subject_alias} and counterparty "
            f"{model_input.counterparty_alias}, referenced only by masked alias."
        ),
        SarFincenElement.WHAT: (
            f"A {_display(catalog, 'txn.channel')} transfer of {_display(catalog, 'txn.amount')} "
            f"in the {_display(catalog, 'txn.direction')} direction."
        ),
        SarFincenElement.WHEN: f"The activity occurred at {_display(catalog, 'txn.occurredAt')}.",
        SarFincenElement.WHERE: f"Origination country {_display(catalog, 'txn.country')}.",
        SarFincenElement.WHY: (
            f"Rules fired: {rule_summary}. Model drivers: {driver_summary}. Regulatory basis: "
            + (
                "; ".join(f"{r.citation_id} — {r.title}" for r in model_input.regulations)
                or _UNKNOWN
            )
        ),
        SarFincenElement.HOW: (
            f"Risk band {_display(catalog, 'risk.band')} at "
            f"{_display(catalog, 'risk.fraudProbability')} calibrated fraud probability."
        ),
    }
    return tuple(
        SarSection(heading=element.value.capitalize(), body=bodies[element])
        for element in SarFincenElement
    )
