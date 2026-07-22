"""GFP scope-stream tests (GFP plan Phase 3): the global stream covers every context
edge; each tenant stream carries ONLY that agency's owned (source-agency) edges; the
tenant streams exactly partition the edges; and per-stream feature blocks concatenate
back into original order with every row restored exactly once — double-fills and holes
fail loudly."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.aml_fraud import demo_agency_index
from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.edges import build_gfp_edge_set, with_targets
from lib.gfp.scopes import (
    concatenate_stream_features,
    global_stream_indices,
    tenant_stream_indices,
    validate_tenant_partition,
)

_CONFIG = load_gfp_benchmark_config()
_AGENCIES = 3


def _edge_set(n: int = 12) -> object:
    rows = [
        {
            "Timestamp": f"2022/09/01 00:{i:02d}",
            "From Bank": f"B{i % 5}",
            "Account": f"S{i}",
            "To Bank": f"B{(i + 2) % 5}",
            "Account.1": f"D{i}",
            "Amount Paid": "10.00",
            "Payment Currency": "US Dollar",
            "Payment Format": "Wire",
            "Is Laundering": str(i % 4 == 0)[:1].replace("T", "1").replace("F", "0"),
        }
        for i in range(n)
    ]
    return build_gfp_edge_set(pd.DataFrame(rows, dtype=str), _CONFIG, agency_count=_AGENCIES)


def test_global_stream_covers_every_context_edge() -> None:
    edges = _edge_set()
    assert global_stream_indices(edges).tolist() == list(range(12))


def test_tenant_streams_carry_only_owned_edges() -> None:
    edges = _edge_set()
    for agency in range(_AGENCIES):
        stream = tenant_stream_indices(edges, agency)
        assert np.all(edges.source_agency[stream] == agency)
        # Ownership is the SOURCE node's agency — the existing demo partition.
        for index in stream.tolist():
            bank = f"B{edges.original_row_id[index] % 5}"
            assert demo_agency_index(bank, _AGENCIES) == agency
    with pytest.raises(ValueError, match="non-negative"):
        tenant_stream_indices(edges, -1)


def test_tenant_partition_is_exact_and_restores_every_target_once() -> None:
    edges = _edge_set()
    streams = validate_tenant_partition(edges, _AGENCIES)
    stacked = np.sort(np.concatenate(streams))
    assert stacked.tolist() == list(range(12))  # disjoint union of ALL context edges
    narrowed = with_targets(edges, np.arange(12) % 2 == 0)
    validate_tenant_partition(narrowed, _AGENCIES)  # targets restored exactly once
    with pytest.raises(ValueError, match=">= 1"):
        validate_tenant_partition(edges, 0)


def test_tenant_partition_fails_when_an_agency_is_missing() -> None:
    edges = _edge_set()
    present = int(np.unique(edges.source_agency).shape[0])
    if present == _AGENCIES:  # drop one agency's stream by validating with fewer agencies
        with pytest.raises(ValueError, match="partition"):
            validate_tenant_partition(edges, _AGENCIES - 1)


def test_concatenate_restores_original_order_exactly_once() -> None:
    edges = _edge_set()
    streams = validate_tenant_partition(edges, _AGENCIES)
    width = 4
    blocks = [
        (stream, np.full((stream.shape[0], width), float(agency), dtype=np.float64))
        for agency, stream in enumerate(streams)
    ]
    combined = concatenate_stream_features(12, blocks)
    assert combined.dtype == np.float32
    assert combined.shape == (12, width)
    for agency, stream in enumerate(streams):
        assert np.all(combined[stream] == float(agency))


def test_concatenate_rejects_double_fill_holes_and_misalignment() -> None:
    indices = np.array([0, 1], dtype=np.int64)
    features = np.ones((2, 3))
    with pytest.raises(ValueError, match="more than one stream"):
        concatenate_stream_features(3, [(indices, features), (indices, features)])
    with pytest.raises(ValueError, match="unfilled rows"):
        concatenate_stream_features(3, [(indices, features)])
    with pytest.raises(ValueError, match="align"):
        concatenate_stream_features(2, [(indices, np.ones((3, 3)))])
    with pytest.raises(ValueError, match="at least one stream"):
        concatenate_stream_features(2, [])
    with pytest.raises(ValueError, match="positive"):
        concatenate_stream_features(0, [(indices, features)])
