"""Summary: Deterministic hallucination recall and clean-draft false-positive quality gate.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Planted unsupported facts cover altered amounts/dates, invented typologies, identities, and ids.
"""

from __future__ import annotations

import pytest
from adversarial_drafts import SUPPORTED_REF, adversarial_draft_cases, clean_draft

from fraudlens_backend.agents.checks import evaluate_draft_checks
from fraudlens_backend.sar.schema import ground_citations
from fraudlens_core.phi import mask_text
from fraudlens_ml.evaluation import false_positive_rate, unsupported_claim_recall
from fraudlens_ml.sar import SarCitation
from lib.quality.config import load_quality_config

pytestmark = pytest.mark.quality
_AVAILABLE = (
    SarCitation(
        citation="31 CFR 1010.314",
        title="Synthetic citation",
        source="FinCEN / Bank Secrecy Act",
        snippet="Synthetic excerpt.",
    ),
)


def test_planted_unsupported_claim_recall_meets_threshold() -> None:
    planted: list[str] = []
    detected: list[str] = []
    for case in adversarial_draft_cases():
        checks = evaluate_draft_checks(
            case.content,
            _AVAILABLE,
            available_evidence_refs={SUPPORTED_REF},
        )
        planted.extend(f"{case.name}:{index}" for index in case.unsupported_indexes)
        detected.extend(f"{case.name}:{index}" for index in checks.unsupported_claim_indexes)
        if case.name == "fabricated-citation":
            assert checks.fabricated_citation_ids == ("FAKE-1",)

    metrics = unsupported_claim_recall(planted, detected)
    assert metrics.rate >= load_quality_config().sar_quality.unsupported_claim_recall_min


def test_clean_draft_false_positive_rate_meets_threshold() -> None:
    content = clean_draft()
    checks = evaluate_draft_checks(
        content,
        _AVAILABLE,
        available_evidence_refs={SUPPORTED_REF},
    )
    flagged = tuple(
        index in checks.unsupported_claim_indexes for index, _ in enumerate(content.claims)
    )
    metrics = false_positive_rate(tuple(True for _ in content.claims), flagged)

    assert checks.passed
    assert metrics.rate <= load_quality_config().sar_quality.clean_draft_false_positive_max


def test_fabricated_citation_and_phi_shaped_account_never_survive() -> None:
    grounded_ids, grounded = ground_citations(("FAKE-1",), _AVAILABLE)
    masked = mask_text("Counterparty account 4111111111111111 received funds.").value

    assert grounded_ids == ()
    assert grounded == ()
    assert "4111111111111111" not in masked
