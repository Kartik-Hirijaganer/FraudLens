"""Summary: Offline cascade-quality gate replaying the shipped gate over the persisted run.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The release was scoped on a projection computed BEFORE the gate existed (plan §Context,
  "Pre-computed evidence"): AWQ 73.9% deterministic pass, BF16 94.1%, cascade 96.1%, escalation
  26.1% at concurrency 32. This suite is what makes those numbers a property of shipped code —
  it replays the real committed outputs through the real `SarQualityGate` and requires the
  reproduced rates to land inside a stated tolerance of the projection. A divergence is a signal
  that the gate no longer matches the analysis the spend was approved against.
- Rates are measured at two scales: the full 1,000-case run (recorded in the corpus provenance
  when it was drawn) and the committed 40-case sample (recomputed here on every run). The sample
  is stratified over the joint (AWQ, BF16) outcomes, so it tracks the population to within one
  case; the tolerance below is that sampling resolution, not a fudge factor.
- Escalation is asserted as a PROPERTY, not a number: every AWQ rejection must be one the cascade
  would actually escalate on (`fallback_required`), otherwise a 26% escalation rate would be
  arithmetic that the drafter never performs.
- The committed replay pilot is checked here too: the 40-case sample proves the gate behaves, and
  the pilot proves the FULL 1,000-case rates the spend was approved against still recompute.
- Outputs are judged through `evaluate_sar_quality`, the same raw-text entry point the benchmark
  and the replay pilot use, so this suite cannot drift into a second definition of "servable".
- No provider, socket, credential, or GPU is involved; the corpus is committed and redacted.
"""

from __future__ import annotations

import pytest
from quality_gates import replay_gate
from sar_replay import (
    AWQ_ARM,
    BF16_ARM,
    REPLAY_PILOT_PATH,
    ReplayArmOutput,
    ReplayCase,
    load_replay_corpus,
)

from fraudlens_backend.sar.evidence import SarEvidenceCatalog
from fraudlens_backend.sar.quality_gate import evaluate_sar_quality
from fraudlens_ml.sar import SarQualityGateResult
from lib.vllm_bench.cascade_pilot import CascadeReplayPilot

pytestmark = pytest.mark.quality

# Plan §Context "Pre-computed evidence (zero GPU spend, from the persisted run)", concurrency 32.
_PROJECTED_AWQ_PASS = 0.739
_PROJECTED_BF16_PASS = 0.941
_PROJECTED_CASCADE_PASS = 0.961
_PROJECTED_ESCALATION = 0.261
# The projection was computed to three decimals off a 1,000-case run; two points of agreement is
# what "the gate matches the analysis" means at that resolution.
_PROJECTION_TOLERANCE = 0.02
# One case out of forty is 0.025, so a stratified sample cannot track its population closer.
_SAMPLING_TOLERANCE = 0.05
_EMPTY_CATALOG = SarEvidenceCatalog(facts=())


def _verdict(case: ReplayCase, output: ReplayArmOutput) -> SarQualityGateResult:
    """Run the shipped gate over one real output through the one shared raw-text entry point."""
    return evaluate_sar_quality(
        replay_gate(),
        output.content,
        available=case.offered_citations(),
        catalog=_EMPTY_CATALOG,
        finish_reason=output.finish_reason,
        available_evidence_refs=case.available_evidence_refs,
    )


def _sample_rates() -> dict[str, float]:
    """Recompute the corpus's arm, cascade, and escalation rates from its real outputs."""
    corpus = load_replay_corpus()
    awq = [_verdict(case, case.arms[AWQ_ARM]).passed for case in corpus.cases]
    bf16 = [_verdict(case, case.arms[BF16_ARM]).passed for case in corpus.cases]
    total = len(corpus.cases)
    return {
        AWQ_ARM: sum(awq) / total,
        BF16_ARM: sum(bf16) / total,
        "cascade": sum(a or b for a, b in zip(awq, bf16, strict=True)) / total,
        "escalation": sum(not a for a in awq) / total,
    }


@pytest.mark.parametrize(
    ("arm", "projected"),
    [(AWQ_ARM, _PROJECTED_AWQ_PASS), (BF16_ARM, _PROJECTED_BF16_PASS)],
)
def test_the_shipped_gate_reproduces_the_projected_arm_pass_rate(
    arm: str, projected: float
) -> None:
    """The gate the cascade runs reproduces the arm pass rate the release was scoped on."""
    population = load_replay_corpus().population

    assert population.pass_rate(arm) == pytest.approx(projected, abs=_PROJECTION_TOLERANCE)
    assert _sample_rates()[arm] == pytest.approx(population.pass_rate(arm), abs=_SAMPLING_TOLERANCE)


def test_the_cascade_passes_more_cases_than_bf16_alone() -> None:
    """Escalation is worth paying for: the cascade serves strictly more cases than BF16 alone."""
    population = load_replay_corpus().population
    rates = _sample_rates()

    assert population.cascade_pass_rate > population.bf16_pass_rate
    assert population.cascade_pass_rate == pytest.approx(
        _PROJECTED_CASCADE_PASS, abs=_PROJECTION_TOLERANCE
    )
    assert rates["cascade"] >= rates[BF16_ARM]


def test_the_escalation_rate_matches_the_projection_and_is_actually_escalatable() -> None:
    """Every rejected AWQ draft is one the cascade escalates on, at the projected rate."""
    corpus = load_replay_corpus()
    rejected = [
        _verdict(case, case.arms[AWQ_ARM])
        for case in corpus.cases
        if not case.arms[AWQ_ARM].expected_passed
    ]

    assert _sample_rates()["escalation"] == pytest.approx(
        _PROJECTED_ESCALATION, abs=_SAMPLING_TOLERANCE
    )
    assert rejected, "the corpus contains no AWQ rejection to escalate"
    assert all(verdict.fallback_required for verdict in rejected)
    assert all(not verdict.passed for verdict in rejected)


def test_the_projection_the_spend_was_approved_against_is_itself_recomputed() -> None:
    """The committed pilot must still reproduce the full-run rates the release was scoped on."""
    corpus = load_replay_corpus()
    pilot = CascadeReplayPilot.model_validate_json(REPLAY_PILOT_PATH.read_bytes())
    level = next(item for item in pilot.levels if item.concurrency == corpus.concurrency)

    assert pilot.policy_version == corpus.policy_version
    assert level.cascade.escalation_rate == pytest.approx(
        _PROJECTED_ESCALATION, abs=_PROJECTION_TOLERANCE
    )
    assert level.cascade.final_pass_rate == pytest.approx(
        _PROJECTED_CASCADE_PASS, abs=_PROJECTION_TOLERANCE
    )
    assert level.arm_pass_rate[AWQ_ARM] == pytest.approx(
        _PROJECTED_AWQ_PASS, abs=_PROJECTION_TOLERANCE
    )
    assert level.arm_pass_rate[BF16_ARM] == pytest.approx(
        _PROJECTED_BF16_PASS, abs=_PROJECTION_TOLERANCE
    )
    # The cascade must still be worth its second tier over the whole run, not only in the sample.
    assert level.cascade.final_pass_rate > level.arm_pass_rate[BF16_ARM]
