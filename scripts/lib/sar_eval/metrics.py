"""Summary: Paired metric aggregation and true BCa confidence intervals for SAR evaluation.
Each scenario contributes one multi-agent-minus-single-writer delta after the three judge
samples are reduced by median; 10,000 fixed-seed paired draws and leave-one-scenario-out
jackknife acceleration produce the two-sided 95% BCa interval.

Key classes:
- BcaInterval: point estimate and bias-corrected accelerated interval.

Key functions:
- bca_mean_interval: compute a deterministic paired BCa interval over scenario deltas.
- pairwise_agreement: agreement across three boolean judge vectors.
- pairwise_exact_agreement: exact pairwise agreement for three counts or span sets.

Notes:
- NormalDist supplies normal CDF/inverse-CDF without a new statistics dependency.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

_MINIMUM_PAIRED_ROWS = 2
_JUDGE_SAMPLE_COUNT = 3


class BcaInterval(BaseModel):
    """Mean paired delta and its bias-corrected accelerated interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    point_estimate: float = Field(..., description="Mean observed paired delta.")
    lower: float = Field(..., description="Lower BCa confidence bound.")
    upper: float = Field(..., description="Upper BCa confidence bound.")


def bca_mean_interval(
    deltas: np.ndarray,
    *,
    resamples: int,
    confidence_level: float,
    seed: int,
) -> BcaInterval:
    """Return a paired BCa interval for the mean of one-dimensional scenario deltas."""
    values = np.asarray(deltas, dtype=np.float64)
    if values.ndim != 1 or values.size < _MINIMUM_PAIRED_ROWS or not np.isfinite(values).all():
        raise ValueError(
            "BCa deltas must be a finite one-dimensional vector with at least two rows"
        )
    rng = np.random.default_rng(seed)
    point = float(values.mean())
    indices = rng.integers(0, values.size, size=(resamples, values.size))
    bootstrap = values[indices].mean(axis=1)
    less = float(np.count_nonzero(bootstrap < point))
    equal = float(np.count_nonzero(bootstrap == point))
    probability = (less + 0.5 * equal) / resamples
    epsilon = 0.5 / resamples
    normal = NormalDist()
    z_zero = normal.inv_cdf(min(max(probability, epsilon), 1.0 - epsilon))

    total = float(values.sum())
    jackknife = (total - values) / (values.size - 1)
    centered = float(jackknife.mean()) - jackknife
    denominator = 6.0 * float(np.sum(centered**2)) ** 1.5
    acceleration = float(np.sum(centered**3)) / denominator if denominator else 0.0

    alpha = (1.0 - confidence_level) / 2.0

    def adjusted(tail: float) -> float:
        z_alpha = normal.inv_cdf(tail)
        divisor = 1.0 - acceleration * (z_zero + z_alpha)
        value = normal.cdf(z_zero + (z_zero + z_alpha) / divisor) if divisor else tail
        return min(max(value, 0.0), 1.0)

    low_q, high_q = adjusted(alpha), adjusted(1.0 - alpha)
    lower, upper = np.quantile(bootstrap, sorted((low_q, high_q)), method="linear")
    return BcaInterval(point_estimate=point, lower=float(lower), upper=float(upper))


def pairwise_agreement(vectors: tuple[tuple[bool, ...], ...]) -> float:
    """Return mean field-level pairwise agreement across exactly three equal boolean vectors."""
    if (
        len(vectors) != _JUDGE_SAMPLE_COUNT
        or not vectors[0]
        or any(len(item) != len(vectors[0]) for item in vectors)
    ):
        raise ValueError("agreement requires exactly three equal non-empty boolean vectors")
    matches = comparisons = 0
    for left, right in ((0, 1), (0, 2), (1, 2)):
        for a, b in zip(vectors[left], vectors[right], strict=True):
            matches += a == b
            comparisons += 1
    return matches / comparisons


def pairwise_exact_agreement(values: tuple[object, ...]) -> float:
    """Return exact-match agreement across the three sample pairs for one decision value."""
    if len(values) != _JUDGE_SAMPLE_COUNT:
        raise ValueError("exact agreement requires exactly three sample values")
    pairs = ((0, 1), (0, 2), (1, 2))
    return sum(values[left] == values[right] for left, right in pairs) / len(pairs)
