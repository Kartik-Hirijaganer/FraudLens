"""Regression: the portfolio-demo story cannot be rebuilt without a queryable RAG index.

The quality-gated SAR cascade (ADR-030) made a citation MANDATORY and made a case retrieval
offered nothing for TERMINAL — no later tier can cite what was never retrieved. That turned a soft
enhancer into a hard precondition of the pinned story, and the deploy of 2026-09-18 found it: the
bootstrap runs on the GitHub runner, the FinCEN/BSA index is baked into the container image, so
retrieval degraded to empty and every SAR the story pins landed `failed`.

These tests pin the whole chain, one link per test, so a future change that re-couples them fails
here with the cause named instead of on a deploy with the symptom named:

    no citations -> SAR `failed` (terminal) -> `sar_unavailable` review flag -> alert
    `pending_review` -> the story's `alert_target: open` is unreachable -> BootstrapRefusedError

and then the guard that now stops it at the top, before any write.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from rag_index import build_offline_rag_index
from sar_inputs import build_sar_input

from fraudlens_backend.db.models import AlertStatus, SarStatus
from fraudlens_backend.db.repositories.alerts import compute_review_flags
from fraudlens_backend.portfolio_demo import PortfolioDemoConfig
from fraudlens_backend.portfolio_demo.bootstrap import BootstrapRefusedError, assert_rag_index
from fraudlens_backend.portfolio_demo.verification import PIPELINE_RAISED_STATUSES
from fraudlens_backend.sar.drafter_mock import MockSarDrafter
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.settings import AppSettings
from fraudlens_core import RiskBand
from fraudlens_ml.sar import SarDraftResult, SarDraftStatus, SarGateReason

_SAR_UNAVAILABLE = "sar_unavailable"


@pytest.fixture
def settings(
    make_settings: Callable[..., AppSettings], story: PortfolioDemoConfig, tmp_path: Path
) -> AppSettings:
    """Return settings on the story's provider modes, pointed at an isolated (unbuilt) index."""
    return make_settings(
        llm_mode=story.execution.llm_mode,
        rag_embedding_mode=story.execution.rag_embedding_mode,
        rag_index_dir=str(tmp_path / "chroma"),
    )


async def _draft(**overrides: object) -> SarDraftResult:
    """Draft one SAR through the mock drafter the story is calibrated on; return the result."""
    result: SarDraftResult | None = None
    async for event in MockSarDrafter(SarPromptTemplate.load()).draft(build_sar_input(**overrides)):
        if event.result is not None:
            result = event.result
    assert result is not None, "the drafter must terminate with a result"
    return result


# --------------------------------------------------------------------------------------------------
# The chain, link by link
# --------------------------------------------------------------------------------------------------


async def test_a_draft_with_nothing_to_cite_fails_its_gate_with_no_tier_left_to_try() -> None:
    """Zero offered citations is a TERMINAL gate failure: escalating buys a reason code, not a SAR.

    This is the link that makes retrieval load-bearing. Everything below follows from it.
    """
    offered = await _draft()
    nothing_offered = await _draft(citations=())

    assert offered.status is SarDraftStatus.DRAFT
    assert nothing_offered.status is SarDraftStatus.FAILED
    assert nothing_offered.quality is not None
    assert SarGateReason.NO_CITATIONS in nothing_offered.quality.reasons
    assert nothing_offered.quality.fallback_required is False


def test_a_failed_sar_forces_the_alert_out_of_the_open_state_the_story_pins(
    story: PortfolioDemoConfig,
) -> None:
    """A `failed` SAR flags `sar_unavailable`, and any flag raises the alert `pending_review`.

    The story pins `alert_target: open` for the three high-band payout hops, and `open` is only
    reachable when the pipeline raises the alert with NO flags — so the flag alone breaks them.
    The band and probability below are a CONFIDENT high row (the shape of those hops), so the
    failed SAR is the only thing that can contribute a flag.
    """
    confident = {
        "risk_band": RiskBand.HIGH,
        "fraud_probability": 0.97,
        "low_confidence_margin": story.probe.low_confidence_margin,
    }
    without_sar_failure = compute_review_flags(sar_status=SarStatus.DRAFT.value, **confident)
    with_sar_failure = compute_review_flags(sar_status=SarStatus.FAILED.value, **confident)

    assert [flag["flag"] for flag in without_sar_failure] == []
    assert [flag["flag"] for flag in with_sar_failure] == [_SAR_UNAVAILABLE]
    # `raise_alert` lands PENDING_REVIEW whenever flags exist, and the bootstrap refuses to fake a
    # status only the pipeline's own alert-raise produces — which is why the rebuild dies here.
    assert AlertStatus.OPEN in PIPELINE_RAISED_STATUSES


def test_the_story_pins_open_alerts_and_no_failed_sar(story: PortfolioDemoConfig) -> None:
    """The precondition exists because the story DECLARES the states a failed SAR cannot reach."""
    assert story.expected.alert_states.get(AlertStatus.OPEN, 0) > 0
    assert story.expected.sar_states.get(SarStatus.FAILED, 0) == 0
    assert story.expected.alert_states.get(AlertStatus.PENDING_REVIEW, 0) == 0


# --------------------------------------------------------------------------------------------------
# The guard that now stops the chain at the top, before any write
# --------------------------------------------------------------------------------------------------


def test_an_absent_index_is_refused_by_name(settings: AppSettings) -> None:
    """The refusal names the index and the command that builds it, not a downstream symptom."""
    with pytest.raises(BootstrapRefusedError, match="RAG index is 'missing'") as refusal:
        assert_rag_index(settings)
    assert "make ingest-rag" in str(refusal.value)


def test_a_built_index_satisfies_the_guard(settings: AppSettings) -> None:
    """The guard passes on exactly what `make ingest-rag` produces — no test-only index shape."""
    build_offline_rag_index(settings)
    assert_rag_index(settings)  # must not raise


def test_an_index_built_in_another_embedding_space_is_refused(
    make_settings: Callable[..., AppSettings], settings: AppSettings
) -> None:
    """A foreign index is refused too: it retrieves, but not the retrieval the story was pinned on.

    Built under one `rag_version` and queried under another, the retriever silently falls back to
    lexical ranking — a different retrieval, and so a different SAR, from the one `expected` was
    calibrated against. That is the same reason `assert_execution_modes` pins the provider modes.
    """
    build_offline_rag_index(settings)
    other_space = make_settings(
        llm_mode=settings.llm_mode,
        rag_embedding_mode=settings.rag_embedding_mode,
        rag_index_dir=settings.rag_index_dir,
        rag_version="rag-v-other",
    )
    with pytest.raises(BootstrapRefusedError, match="RAG index is 'mismatch'"):
        assert_rag_index(other_space)
