"""Summary: Investigation-pipeline and analyst-review fields mixed into AppSettings.

Key classes:
- InvestigationRuntimeFields: typed history, retrieval, batch, and review settings.

Key functions:
- (none)

Notes:
- Durable worker lease settings join this responsibility-owned model in Phase 8.
"""

from pydantic import BaseModel, Field


class InvestigationRuntimeFields(BaseModel):
    """Typed investigation and review settings shared by AppSettings."""

    investigation_history_window_hours: int = Field(
        default=168,
        gt=0,
        description="Same-account history lookback covering the widest built-in rule window.",
    )
    investigation_history_max: int = Field(
        default=100,
        gt=0,
        description="Maximum same-account history rows loaded per investigation.",
    )
    investigation_rag_top_k: int = Field(
        default=4,
        gt=0,
        description="Number of FinCEN/BSA chunks retrieved for investigation citations.",
    )
    investigation_rag_min_similarity: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity required to surface a vector RAG citation.",
    )
    batch_score_limit: int = Field(
        default=2000,
        gt=0,
        description="Maximum un-investigated transactions processed by one batch-score sweep.",
    )
    review_low_confidence_margin: float = Field(
        default=0.1,
        gt=0,
        le=0.5,
        description="Decision-boundary half-width that forces analyst review.",
    )
    sar_pdf_max_attempts: int = Field(
        default=3,
        gt=0,
        description="Maximum best-effort SAR PDF generation attempts.",
    )
