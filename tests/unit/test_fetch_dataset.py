"""Dataset-fetch tests (real-AML training plan Phase 2 + Verification #2: "unit-test
`_verify_present` + prod refusal; mock the network"; GFP study plan Phase 2 adds the Medium
registry variants + richer PHI-free provenance). The fetch script pulls ONE registry-named
Kaggle variant into the gitignored data dir and returns PHI-free provenance; these tests
exercise verification, the skip/force flow, the prod refusal, and the token guard WITHOUT ever
hitting the network."""

from __future__ import annotations

from pathlib import Path

import pytest

import fetch_dataset
from fetch_dataset import (
    IBM_AML,
    IBM_AML_HI_MEDIUM,
    IBM_AML_LI_MEDIUM,
    DatasetSpec,
    _kaggle_download,
    _verify_present,
    dataset_spec,
    download,
)
from fraudlens_backend.settings import AppSettings

_SPEC = DatasetSpec(source="test", slug="owner/dataset", variant="sample.csv", license="CDLA-1.0")


def _write_csv(directory: Path, rows: int) -> Path:
    """Write a small canonical-shaped CSV (header + `rows` data rows) and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _SPEC.variant
    body = "\n".join(f"DEMO-{i},{i}00,USD" for i in range(rows))
    target.write_text("externalId,amount,currency\n" + body + "\n", encoding="utf-8")
    return target


def test_dataset_spec_resolves_ibm_and_rejects_unknown() -> None:
    spec = dataset_spec(IBM_AML)
    assert spec.slug == "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
    assert spec.variant == "HI-Small_Trans.csv"  # single file, never the ~8 GB bundle
    assert spec.license == "CDLA-Sharing-1.0"
    with pytest.raises(KeyError):
        dataset_spec("does-not-exist")


def test_dataset_spec_resolves_gfp_medium_variants() -> None:
    """GFP plan Phase 2: Medium variants are typed registry entries; ibm-aml is preserved."""
    hi = dataset_spec(IBM_AML_HI_MEDIUM)
    li = dataset_spec(IBM_AML_LI_MEDIUM)
    assert hi.variant == "HI-Medium_Trans.csv"  # still one file per fetch, never the bundle
    assert li.variant == "LI-Medium_Trans.csv"
    small = dataset_spec(IBM_AML)
    for spec in (small, hi, li):
        assert spec.slug == small.slug  # one AMLworld bundle, three single-file variants
        assert spec.license == "CDLA-Sharing-1.0"
        assert spec.label_column == "Is Laundering"  # enables illicit-ratio provenance
        assert spec.timestamp_column == "Timestamp"  # enables time-range provenance


def test_verify_present_returns_phi_free_provenance(tmp_path: Path) -> None:
    _write_csv(tmp_path, rows=3)
    paths = _verify_present(_SPEC, tmp_path)
    assert paths.source == "test"
    assert len(paths.files) == 1
    fetched = paths.files[0]
    assert fetched.name == "sample.csv"
    assert fetched.row_count == 3  # header excluded
    assert len(fetched.sha256) == 64  # full sha-256 hex digest
    # PHI-free: provenance is names/hashes/counts only — no raw row content leaks.
    assert "DEMO-0" not in paths.model_dump_json()


def test_verify_present_computes_label_and_time_provenance(tmp_path: Path) -> None:
    """With provenance columns configured, one scan yields counts, ratio, and time range."""
    spec = DatasetSpec(
        source="test-aml",
        slug="owner/dataset",
        variant="sample.csv",
        license="CDLA-1.0",
        label_column="Is Laundering",
        timestamp_column="Timestamp",
    )
    target = tmp_path / spec.variant
    target.write_text(
        "Timestamp,Account,Is Laundering\n"
        "2022/09/01 00:20,ACC-B,0\n"
        "2022/09/03 08:00,ACC-A,1\n"  # timestamps deliberately out of order
        "2022/09/02 12:30,ACC-C,0\n"
        "2022/09/01 00:05,ACC-D,1\n",
        encoding="utf-8",
    )
    fetched = _verify_present(spec, tmp_path).files[0]
    assert fetched.row_count == 4
    assert fetched.illicit_row_count == 2
    assert fetched.illicit_ratio == pytest.approx(0.5)
    assert fetched.first_timestamp == "2022/09/01 00:05"
    assert fetched.last_timestamp == "2022/09/03 08:00"
    # PHI-free: counts, ratio, and timestamps only — no account tokens leak into provenance.
    assert "ACC-" not in fetched.model_dump_json()


def test_verify_present_fails_loud_when_provenance_column_missing(tmp_path: Path) -> None:
    """A configured provenance column absent from the header is an error, never a skip."""
    spec = DatasetSpec(
        source="test-aml",
        slug="owner/dataset",
        variant="sample.csv",
        license="CDLA-1.0",
        label_column="Is Laundering",
    )
    (tmp_path / spec.variant).write_text("Timestamp,Account\nx,y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Is Laundering"):
        _verify_present(spec, tmp_path)


def test_verify_present_without_provenance_columns_keeps_stats_unset(tmp_path: Path) -> None:
    """Specs without label/timestamp columns keep the optional provenance fields None."""
    _write_csv(tmp_path, rows=2)
    fetched = _verify_present(_SPEC, tmp_path).files[0]
    assert fetched.illicit_row_count is None
    assert fetched.illicit_ratio is None
    assert fetched.first_timestamp is None
    assert fetched.last_timestamp is None


def test_verify_present_raises_when_absent_or_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _verify_present(_SPEC, tmp_path)  # nothing downloaded yet
    (tmp_path / _SPEC.variant).write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        _verify_present(_SPEC, tmp_path)  # present but empty is treated as absent


def test_download_invokes_runner_then_verifies(tmp_path: Path) -> None:
    calls: list[bool] = []

    def fake_runner(spec: DatasetSpec, directory: Path, force: bool) -> None:
        calls.append(force)
        _write_csv(directory, rows=2)

    paths = download(_SPEC, tmp_path, runner=fake_runner)
    assert calls == [False]  # ran once, not forced
    assert paths.files[0].row_count == 2


def test_download_skips_when_present_unless_forced(tmp_path: Path) -> None:
    _write_csv(tmp_path, rows=1)
    calls: list[bool] = []

    def fake_runner(spec: DatasetSpec, directory: Path, force: bool) -> None:
        calls.append(force)
        _write_csv(directory, rows=5)

    # Present -> runner is skipped.
    assert download(_SPEC, tmp_path, runner=fake_runner).files[0].row_count == 1
    assert calls == []
    # force=True -> runner re-runs.
    assert download(_SPEC, tmp_path, force=True, runner=fake_runner).files[0].row_count == 5
    assert calls == [True]


def test_kaggle_download_requires_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="KAGGLE_API_TOKEN"):
        _kaggle_download(_SPEC, tmp_path, force=False)


def test_main_refuses_in_prod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        fetch_dataset,
        "get_settings",
        lambda: AppSettings(environment="prod", aml_data_dir=str(tmp_path)),
    )
    # A runner that would explode if ever called — the prod refusal must short-circuit first.
    monkeypatch.setattr(
        fetch_dataset, "_run_kaggle", lambda *a, **k: pytest.fail("must not fetch in prod")
    )
    assert fetch_dataset.main(["--source", IBM_AML]) == 1
    assert not (tmp_path / "HI-Small_Trans.csv").exists()


def test_main_happy_path_dev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        fetch_dataset,
        "get_settings",
        lambda: AppSettings(environment="dev", aml_data_dir=str(tmp_path)),
    )

    def fake_run(spec: DatasetSpec, directory: Path, force: bool) -> None:
        # Registry-shaped mini CSV: ibm-aml pins label/timestamp provenance columns.
        (directory / spec.variant).write_text(
            "Timestamp,From Bank,Account,Is Laundering\n2022/09/01 00:20,010,DEMO-1,0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(fetch_dataset, "_run_kaggle", fake_run)
    assert fetch_dataset.main([]) == 0  # defaults to --source ibm-aml
    assert (tmp_path / "HI-Small_Trans.csv").exists()


def test_main_reports_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        fetch_dataset,
        "get_settings",
        lambda: AppSettings(environment="dev", aml_data_dir=str(tmp_path)),
    )

    def boom(spec: DatasetSpec, directory: Path, force: bool) -> None:
        raise RuntimeError("kaggle download failed")

    monkeypatch.setattr(fetch_dataset, "_run_kaggle", boom)
    assert fetch_dataset.main([]) == 1
