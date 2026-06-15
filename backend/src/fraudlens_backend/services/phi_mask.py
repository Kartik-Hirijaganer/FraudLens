"""Summary: The ingest-time PHI masking service (plan §8.2, §6.6, ADR-014). `PhiMasker`
turns a validated `CanonicalTransaction` into a `MaskedTransaction` — the only form that
is ever persisted: the origin/destination account identifiers are masked to their last
four characters, any string-valued feature is scrubbed of PHI-shaped spans, and a
deterministic `feature_hash` is computed so the raw identifiers never need to be stored.
This is the single place ingestion (the API endpoint and the IEEE-CIS importer) calls so
"store masked, never raw" is enforced by construction rather than per call site. Masking is
deterministic and in-process (fraudlens-core); the optional Presidio NER enhancer for
free-text fields (gated by the `phiNerMasking` flag) layers in later, when free-text inputs
such as analyst notes arrive — Phase 3 stores only structured, deterministically-masked data.

Key classes:
- MaskedTransaction: the masked, hash-bearing record that is safe to persist.
- PhiMasker: masks a CanonicalTransaction's identifiers + features and hashes its content.

Key functions:
- (none)

Notes:
- The aggregate MaskingReport is counts-only (category -> count); it carries no values, so
  it can flow into an audit row or a log line without leaking PHI (the audit write itself
  is consolidated in Phase 12).
- mask() reads only the CanonicalTransaction, so the raw account strings live solely in
  memory for the duration of the call and are never returned or persisted.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_core import CanonicalTransaction, compute_feature_hash
from fraudlens_core.phi import MaskingReport, mask_identifier, mask_text


class MaskedTransaction(BaseModel):
    """The masked, hash-bearing projection of a transaction — the only form persisted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin_account: str = Field(..., description="Origin account masked to its last four.")
    dest_account: str = Field(..., description="Destination account masked to its last four.")
    features: dict[str, Any] = Field(
        default_factory=dict, description="Features with PHI-shaped string values scrubbed."
    )
    feature_hash: str = Field(..., description="Deterministic PHI-free content fingerprint.")
    report: MaskingReport = Field(..., description="Counts-only aggregate masking report.")


class PhiMasker:
    """Masks a CanonicalTransaction into the persist-safe MaskedTransaction form."""

    def mask(self, canonical: CanonicalTransaction) -> MaskedTransaction:
        """Mask account identifiers + string features and compute the feature hash."""
        counts: Counter[str] = Counter()
        origin = mask_identifier(canonical.origin_account)
        dest = mask_identifier(canonical.dest_account)
        counts.update(origin.report.categories)
        counts.update(dest.report.categories)

        masked_features: dict[str, Any] = {}
        for key, value in canonical.features.items():
            if isinstance(value, str):
                scrubbed = mask_text(value)
                masked_features[key] = scrubbed.value
                counts.update(scrubbed.report.categories)
            else:
                masked_features[key] = value

        return MaskedTransaction(
            origin_account=origin.value,
            dest_account=dest.value,
            features=masked_features,
            feature_hash=compute_feature_hash(canonical),
            report=MaskingReport(
                categories=dict(sorted(counts.items())), total=sum(counts.values())
            ),
        )
