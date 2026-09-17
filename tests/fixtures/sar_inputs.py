"""The one PHI-free `SarInput` factory the SAR tests build cases from.

It lives here rather than inside `conftest.py` so module-level fixtures — model output a drafter
test feeds through a fake adapter, for instance — can build the same case the `make_sar_input`
fixture yields, instead of a second hand-maintained copy that would silently drift from it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fraudlens_core import AmlRuleType, RiskBand, TransactionDirection
from fraudlens_core.rules.base import RuleHit
from fraudlens_ml.rag.citations import escape_as_data
from fraudlens_ml.rag.ingest import chunk_corpus, load_corpus
from fraudlens_ml.sar import SarCitation, SarFeature, SarInput

_REPO_ROOT = Path(__file__).resolve().parents[2]
STRUCTURING_CITATION_ID = "31 CFR 1010.314"


def structuring_citation() -> SarCitation:
    """Return one exact, digest-verifiable citation from the committed corpus."""
    chunks = chunk_corpus(load_corpus(_REPO_ROOT / "data" / "regulations"))
    chunk = next(item for item in chunks if item.citation == STRUCTURING_CITATION_ID)
    return SarCitation(
        citation=chunk.citation,
        title=chunk.title,
        source=chunk.source,
        snippet=escape_as_data(chunk.text),
    )


def build_sar_input(**overrides: Any) -> SarInput:
    """Build the standard PHI-free SarInput used across the SAR-drafting tests."""
    params: dict[str, Any] = {
        "agency_id": "agency-1",
        "transaction_id": "txn-1",
        "source": "synthetic-generator",
        "risk_band": RiskBand.HIGH,
        "fraud_probability": 0.91,
        "amount": Decimal("9500.00"),
        "currency": "USD",
        "country": "US",
        "channel": "wire",
        "direction": TransactionDirection.OUTBOUND,
        "occurred_at": datetime(2024, 6, 1, 14, 0, tzinfo=UTC),
        "model_version": "v0-fixture",
        "rules_version": "rules-abc",
        "rag_version": "rag-v1",
        "rule_hits": (
            RuleHit(
                code="STRUCT",
                rule_type=AmlRuleType.STRUCTURING,
                severity="high",
                weight=Decimal("1.0"),
                reason="Multiple sub-threshold deposits",
            ),
        ),
        "top_features": (
            SarFeature(feature="amount_log", value=9.16, shap_value=0.8),
            SarFeature(feature="velocity_24h", value=5.0, shap_value=-0.2),
        ),
        "citations": (structuring_citation(),),
        "rag_context": "<<REGS>>\n[31 CFR 1010.314] Structuring\nsafe reference text\n<<END>>",
    }
    params.update(overrides)
    return SarInput(**params)
