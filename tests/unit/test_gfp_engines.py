"""GFP engine-seam tests (GFP plan Phase 4): the protocol's shared batch validation, the
deterministic fake (schema-correct but REQUIRED to fail the known-answer graph tests),
and the snapml adapter exercised through an injected stub engine — parameter projection,
width validation, edge-id realignment, float32 narrowing, input copying — so the adapter
logic is fully covered without the x86-64 wheel (the single lazy import stays untested
here and is proven by `make gfp-test`)."""

from __future__ import annotations

import numpy as np
import pytest

from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.fake import FakeGraphPreprocessor
from lib.gfp.protocol import GraphPreprocessor, validate_edge_batch
from lib.gfp.reference import ReferenceGraphPreprocessor
from lib.gfp.schema import GraphFeatureConfig
from lib.gfp.snapml_adapter import SnapMlGraphPreprocessor, snapml_params

_CONFIG = GraphFeatureConfig.from_benchmark(load_gfp_benchmark_config())
_WIDTH = 235  # engineered width for the committed pins (probed)

_FAN_CASE = np.asarray(
    [
        [0, 10, 99, 1000, 5.0],
        [1, 11, 99, 1100, 6.0],
        [2, 12, 99, 1200, 7.0],
    ],
    dtype=np.float64,
)


# ------------------------------------------------------------------- protocol seam
def test_validate_edge_batch_rejects_bad_wire_shapes() -> None:
    with pytest.raises(ValueError, match="2-D"):
        validate_edge_batch(np.zeros(5))
    with pytest.raises(ValueError, match="columns"):
        validate_edge_batch(np.zeros((2, 4)))
    with pytest.raises(ValueError, match="floating"):
        validate_edge_batch(np.zeros((2, 5), dtype=np.int64))
    bad = _FAN_CASE.copy()
    bad[0, 4] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        validate_edge_batch(bad)
    big = _FAN_CASE.copy()
    big[0, 3] = float(1 << 53)
    with pytest.raises(ValueError, match="2\\^53"):
        validate_edge_batch(big)
    dup = _FAN_CASE.copy()
    dup[1, 0] = dup[0, 0]
    with pytest.raises(ValueError, match="unique"):
        validate_edge_batch(dup)
    validate_edge_batch(np.empty((0, 5), dtype=np.float64))  # empty batches are valid


def test_every_engine_satisfies_the_protocol() -> None:
    stub = _StubEngine()
    engines = (
        ReferenceGraphPreprocessor(_CONFIG),
        FakeGraphPreprocessor(_CONFIG),
        SnapMlGraphPreprocessor(_CONFIG, engine_factory=lambda: stub),
    )
    for engine in engines:
        assert isinstance(engine, GraphPreprocessor)
        assert len(engine.feature_names) == _WIDTH


# ------------------------------------------------------------------- fake engine
def test_fake_is_schema_correct_deterministic_and_stateless() -> None:
    fake = FakeGraphPreprocessor(_CONFIG)
    out = fake.transform_batch(_FAN_CASE)
    assert out.shape == (3, _WIDTH)
    assert np.array_equal(out, FakeGraphPreprocessor(_CONFIG).transform_batch(_FAN_CASE))
    split = np.vstack([fake.transform_batch(_FAN_CASE[:1]), fake.transform_batch(_FAN_CASE[1:])])
    assert np.array_equal(out, split)  # stateless by design (a known-answer breaker)
    assert fake.transform_batch(np.empty((0, 5), dtype=np.float64)).shape == (0, _WIDTH)


def test_fake_must_fail_the_known_answer_graph_tests() -> None:
    """The fake can never impersonate a real engine on the pinned known answers."""
    reference = ReferenceGraphPreprocessor(_CONFIG).transform_batch(_FAN_CASE)
    fake = FakeGraphPreprocessor(_CONFIG).transform_batch(_FAN_CASE)
    assert not np.allclose(reference, fake)


# ------------------------------------------------------------------- adapter (stubbed)
class _StubEngine:
    """Deterministic stand-in for snapml's GraphFeaturePreprocessor."""

    def __init__(self, *, reorder: bool = False, drop: bool = False, width: int = _WIDTH):
        self.params: dict[str, object] | None = None
        self.seen: list[np.ndarray] = []
        self._reorder = reorder
        self._drop = drop
        self._width = width

    def set_params(self, params: dict[str, object]) -> None:
        self.params = params

    def transform(self, batch: np.ndarray) -> np.ndarray:
        self.seen.append(batch)
        out = np.hstack([batch, np.tile(batch[:, :1], (1, self._width))])
        if self._drop and out.shape[0] > 1:
            out = out[:-1]
        if self._reorder and out.shape[0] > 1:
            out = out[::-1]
        return out


def test_snapml_params_projects_the_pinned_keys() -> None:
    params = snapml_params(_CONFIG, num_threads=2)
    assert params["time_window"] == 86400
    assert params["scatter-gather_tw"] == 21600
    assert params["fan_bins"] == list(range(2, 31))
    assert params["lc-cycle_bins"] == list(range(2, 11))
    assert params["lc-cycle_len"] == 10
    assert params["vertex_stats_cols"] == [3, 4]
    assert params["vertex_stats_feats"] == [0, 1, 2, 3, 4, 8, 9, 10]
    assert params["num_threads"] == 2


def test_snapml_params_rejects_misordered_configs() -> None:
    reordered = _CONFIG.model_copy(
        update={
            "vertex_stats": _CONFIG.vertex_stats.model_copy(
                update={"endpoints": ("target_in", "source_out", "source_in", "target_out")}
            )
        }
    )
    with pytest.raises(ValueError, match="emission layout"):
        snapml_params(reordered, num_threads=1)
    misordered_stats = _CONFIG.model_copy(
        update={
            "vertex_stats": _CONFIG.vertex_stats.model_copy(
                update={
                    "stats": ("degree", "fan", "ratio", "avg", "sum", "var", "skew", "kurtosis")
                }
            )
        }
    )
    with pytest.raises(ValueError, match="code order"):
        snapml_params(misordered_stats, num_threads=1)


def test_adapter_copies_input_validates_width_and_narrows_to_float32() -> None:
    stub = _StubEngine()
    adapter = SnapMlGraphPreprocessor(_CONFIG, engine_factory=lambda: stub)
    snapshot = _FAN_CASE.copy()
    out = adapter.transform_batch(_FAN_CASE)
    assert np.array_equal(_FAN_CASE, snapshot)  # caller batch untouched
    assert stub.seen[0] is not _FAN_CASE  # engine received a defensive copy
    assert out.dtype == np.float32
    assert out.shape == (3, _WIDTH)  # identifiers dropped, engineered only
    assert stub.params is not None and stub.params["lc-cycle_len"] == 10


def test_adapter_realigns_rows_by_edge_id() -> None:
    adapter = SnapMlGraphPreprocessor(_CONFIG, engine_factory=lambda: _StubEngine(reorder=True))
    out = adapter.transform_batch(_FAN_CASE)
    # The stub fills every engineered cell with the row's edge id: realignment must
    # restore input order 0, 1, 2.
    assert out[:, 0].tolist() == [0.0, 1.0, 2.0]


def test_adapter_fails_loudly_on_width_or_id_mismatches() -> None:
    with pytest.raises(ValueError, match="shape"):
        SnapMlGraphPreprocessor(
            _CONFIG, engine_factory=lambda: _StubEngine(width=_WIDTH - 1)
        ).transform_batch(_FAN_CASE)
    with pytest.raises(ValueError, match="shape"):
        SnapMlGraphPreprocessor(
            _CONFIG, engine_factory=lambda: _StubEngine(drop=True)
        ).transform_batch(_FAN_CASE)
