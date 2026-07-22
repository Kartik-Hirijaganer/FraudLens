"""Real-snapml adapter parity tests (GFP plan Phase 4; run by `make gfp-test` on x86-64,
typically inside `make gfp-container`). Skipped when snapml is absent UNLESS
GFP_REQUIRE_SNAPML=1 (the gfp-test target sets it, so that target FAILS rather than
skips). Verifies the adapter against the pure reference engine on every pinned
known-answer case, batch-by-batch (streaming state) and single-shot, plus width/order,
dtype, and id-dropping."""

from __future__ import annotations

import os

import numpy as np
import pytest

from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.reference import ReferenceGraphPreprocessor
from lib.gfp.schema import GraphFeatureConfig

_CONFIG = GraphFeatureConfig.from_benchmark(load_gfp_benchmark_config())
_REQUIRE = os.environ.get("GFP_REQUIRE_SNAPML") == "1"


def _snapml_adapter() -> object:
    """Import-or-skip (or import-or-FAIL under make gfp-test) the real adapter."""
    try:
        import snapml  # noqa: F401, PLC0415  (presence check only)
    except ImportError:
        if _REQUIRE:
            pytest.fail(
                "snapml is required by `make gfp-test` but is not installed — run inside "
                "`make gfp-container CMD='make gfp-test'` on arm64 hosts"
            )
        pytest.skip("snapml not installed (x86-64 only); exercised via make gfp-test")
    from lib.gfp.snapml_adapter import SnapMlGraphPreprocessor  # noqa: PLC0415

    return SnapMlGraphPreprocessor(_CONFIG, num_threads=2)


def _edges(rows: list[list[float]]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)


_KNOWN_ANSWER_CASES: dict[str, np.ndarray] = {
    "fan": _edges(
        [
            [0, 10, 99, 1000, 5.0],
            [1, 11, 99, 1100, 6.0],
            [2, 12, 99, 1200, 7.0],
            [3, 10, 20, 1300, 8.0],
            [4, 10, 21, 1400, 9.0],
        ]
    ),
    "cycle": _edges(
        [
            [0, 1, 2, 100, 10.0],
            [1, 2, 3, 200, 11.0],
            [2, 3, 1, 300, 12.0],
            [3, 4, 5, 200000, 13.0],
        ]
    ),
    "edge_cases": _edges(
        [
            [0, 7, 7, 500, 1.0],
            [1, 8, 9, 600, 2.0],
            [2, 8, 9, 600, 3.0],
            [3, 9, 8, 600, 4.0],
        ]
    ),
    "scatter_gather": _edges(
        [
            [0, 1, 10, 100, 1.0],
            [1, 1, 11, 200, 2.0],
            [2, 1, 12, 150, 5.0],
            [3, 10, 2, 300, 3.0],
            [4, 11, 2, 400, 4.0],
        ]
    ),
    "scatter_after_gather": _edges(
        [
            [0, 10, 2, 100, 1.0],
            [1, 11, 2, 150, 2.0],
            [2, 1, 10, 200, 3.0],
            [3, 1, 11, 250, 4.0],
        ]
    ),
    "overflow": _edges([[i, 700 + i, 600, 1000 + i, 1.0] for i in range(31)]),
    "boundary": _edges([[0, 1, 2, 1000, 1.0], [1, 3, 2, 1000 + 86400, 2.0]]),
    "two_cycle": _edges([[0, 5, 6, 100, 1.0], [1, 6, 5, 200, 2.0]]),
}

# Documented parity tolerance: histogram counts are exact; population moments differ
# only by float accumulation order between C++ and numpy.
_PARITY = {"rtol": 1e-6, "atol": 1e-8}


@pytest.mark.parametrize("case", sorted(_KNOWN_ANSWER_CASES))
def test_adapter_matches_reference_single_batch(case: str) -> None:
    adapter = _snapml_adapter()
    reference = ReferenceGraphPreprocessor(_CONFIG)
    batch = _KNOWN_ANSWER_CASES[case]
    actual = adapter.transform_batch(batch)  # type: ignore[attr-defined]
    expected = reference.transform_batch(batch)
    assert actual.shape == expected.shape
    np.testing.assert_allclose(actual, expected, **_PARITY)


@pytest.mark.parametrize("batch_size", [1, 2, 128])
def test_adapter_matches_reference_across_batch_splits(batch_size: int) -> None:
    adapter = _snapml_adapter()
    reference = ReferenceGraphPreprocessor(_CONFIG)
    stream = _KNOWN_ANSWER_CASES["fan"]
    actual_rows = []
    expected_rows = []
    for start in range(0, stream.shape[0], batch_size):
        chunk = stream[start : start + batch_size]
        actual_rows.append(adapter.transform_batch(chunk))  # type: ignore[attr-defined]
        expected_rows.append(reference.transform_batch(chunk))
    np.testing.assert_allclose(np.vstack(actual_rows), np.vstack(expected_rows), **_PARITY)


def test_adapter_output_contract() -> None:
    adapter = _snapml_adapter()
    batch = _KNOWN_ANSWER_CASES["fan"]
    out = adapter.transform_batch(batch)  # type: ignore[attr-defined]
    assert out.dtype == np.float32  # engineered columns only, identifiers dropped
    assert out.shape == (batch.shape[0], len(adapter.feature_names))  # type: ignore[attr-defined]
    assert np.isfinite(out).all()
