"""Summary: IBM final-test selection and Phase-6 dependency-boundary tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Tiny DuckDB fixtures cover schema reconstruction; real full-data artifacts are not required.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from vllm_bench_fakes import benchmark_case

from fraudlens_ml.sar import SarInput
from lib.fulldata.config import load_fulldata_config
from lib.vllm_bench.cases_ibm import (
    _balanced,
    _candidate_rows,
    _context,
    _file_sha256,
    _Prepared,
    build_ibm_cases,
)
from lib.vllm_bench.config import load_config


def _parquet(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    connection.execute(
        """
        CREATE TABLE source AS SELECT * FROM (VALUES
          (1, TIMESTAMP '2024-01-01 12:00:00', 'a', 'b', 100.0, 'US Dollar', 'Wire'),
          (2, TIMESTAMP '2024-01-01 13:00:00', 'a', 'c', 200.0, 'US Dollar', 'ACH'),
          (3, TIMESTAMP '2024-01-01 14:00:00', 'd', 'a', 300.0, 'US Dollar', 'Wire')
        ) t(row_id, occurred_at, origin_account, dest_account, amount,
            payment_currency, payment_format)
        """
    )
    connection.execute(f"COPY source TO '{path}' (FORMAT PARQUET)")


def test_candidate_rows_and_context_reconstruct_bounded_history(sandbox: Path) -> None:
    source = sandbox / "source.parquet"
    folds = sandbox / "folds.parquet"
    with duckdb.connect() as connection:
        _parquet(connection, source)
        connection.execute("CREATE TABLE folds AS SELECT row_id, 'holdout' fold FROM source")
        connection.execute(f"COPY folds TO '{folds}' (FORMAT PARQUET)")
        rows = _candidate_rows(
            connection,
            fold_path=folds,
            source_scan=str(source),
            seed=1729,
            limit=3,
        )
        current = max(rows, key=lambda row: row["occurred_at"])
        context = _context(
            connection,
            current,
            source_scan=str(source),
            hours=24,
            limit=10,
        )
    assert len(rows) == 3
    assert context.transaction.amount == 300
    assert context.counterparty_history
    assert _file_sha256(source) == _file_sha256(source)


def test_balanced_selection_prefers_distinct_subjects() -> None:
    case_a = benchmark_case("a")
    case_b = benchmark_case("b").model_copy(update={"amount_band": "1k-10k"})
    prepared = [
        _Prepared(case=case_a, sar_input=None, subject_key="same"),  # type: ignore[arg-type]
        _Prepared(case=case_b, sar_input=None, subject_key="distinct"),  # type: ignore[arg-type]
        _Prepared(
            case=case_b.model_copy(update={"case_id": "c"}), sar_input=None, subject_key="same"
        ),  # type: ignore[arg-type]
    ]
    selected = _balanced(prepared, 2)
    assert {item.subject_key for item in selected} == {"same", "distinct"}


def test_ibm_builder_stops_at_phase_6_boundary(sandbox: Path, monkeypatch) -> None:
    config = load_config()
    full = load_fulldata_config(Path(config.cases.fulldata_config))
    monkeypatch.setattr("lib.vllm_bench.cases_ibm.load_fulldata_config", lambda _path: full)
    with pytest.raises(FileNotFoundError, match="Phase 6 application-candidate artifact"):
        build_ibm_cases(config, profile="full", repo_root=sandbox)
    with pytest.raises(ValueError, match="profile=full"):
        build_ibm_cases(config, profile="smoke", repo_root=sandbox)


def test_ibm_builder_assembles_exact_sets_when_phase_6_inputs_exist(
    sandbox: Path,
    monkeypatch,
    make_sar_input: Callable[..., SarInput],
) -> None:
    config = load_config()
    cases = config.cases.model_copy(
        update={
            "count": 3,
            "quotas": {"hi-small": 1, "hi-medium": 1, "li-medium": 1},
            "dev_count": 1,
            "warmup_count": 1,
            "abstention_fixtures": 1,
        }
    )
    config = config.model_copy(update={"cases": cases})
    artifact_file = sandbox / "artifact.bin"
    artifact_file.write_bytes(b"phase-six")
    monkeypatch.setattr(
        "lib.vllm_bench.cases_ibm.load_candidate_evaluation",
        lambda *_args: SimpleNamespace(model_bundle="v0-fixture", model_sha256="b" * 64),
    )
    monkeypatch.setattr("lib.vllm_bench.cases_ibm.folded_path", lambda *_args: artifact_file)
    monkeypatch.setattr("lib.vllm_bench.cases_ibm.ingested_scan", lambda *_args: "scan")
    monkeypatch.setattr("lib.vllm_bench.cases_ibm._retriever", lambda _root: object())
    monkeypatch.setattr(
        "lib.vllm_bench.cases_ibm._candidate_rows",
        lambda *_args, **_kwargs: [{"origin_account": f"subject-{index}"} for index in range(3)],
    )
    monkeypatch.setattr("lib.vllm_bench.cases_ibm._context", lambda *_args, **_kwargs: object())

    def prepared(*_args, **kwargs):
        dataset = kwargs["dataset_name"]
        ordinal = kwargs["ordinal"]
        case = benchmark_case(f"ibm-{dataset}-{ordinal}").model_copy(
            update={"source_dataset": dataset}
        )
        return _Prepared(
            case=case,
            sar_input=make_sar_input(source="ibm-aml-synthetic"),
            subject_key=f"{dataset}-{ordinal}",
        )

    monkeypatch.setattr("lib.vllm_bench.cases_ibm._prepared_case", prepared)
    artifact = build_ibm_cases(config, profile="full", repo_root=Path.cwd())
    counts = {
        kind: sum(case.case_set == kind for case in artifact.cases)
        for kind in ("measured", "development", "warmup", "abstention")
    }
    assert counts == {"measured": 3, "development": 1, "warmup": 1, "abstention": 1}
    assert artifact.distinct_subjects == 3
    assert artifact.subject_overlap == 0


def test_ibm_builder_reports_explicit_unfillable_quota(sandbox: Path, monkeypatch) -> None:
    config = load_config()
    cases = config.cases.model_copy(update={"count": 1, "quotas": {"hi-small": 1}})
    config = config.model_copy(update={"cases": cases})
    artifact_file = sandbox / "artifact.bin"
    artifact_file.write_bytes(b"phase-six")
    monkeypatch.setattr(
        "lib.vllm_bench.cases_ibm.load_candidate_evaluation",
        lambda *_args: SimpleNamespace(model_bundle="v0-fixture", model_sha256="b" * 64),
    )
    monkeypatch.setattr("lib.vllm_bench.cases_ibm.folded_path", lambda *_args: artifact_file)
    monkeypatch.setattr("lib.vllm_bench.cases_ibm.ingested_scan", lambda *_args: "scan")
    monkeypatch.setattr("lib.vllm_bench.cases_ibm._retriever", lambda _root: object())
    monkeypatch.setattr("lib.vllm_bench.cases_ibm._candidate_rows", lambda *_args, **_kwargs: [])
    with pytest.raises(ValueError, match=r"quota deficient.*required 1, eligible 0"):
        build_ibm_cases(config, profile="full", repo_root=Path.cwd())
