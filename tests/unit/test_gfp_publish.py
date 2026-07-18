"""GFP publish tests (GFP plan Phases 5-6): run-directory round-trips, every
publication gate (snapml-only engine, complete evaluation grid, all three motifs,
redaction fail-closed), atomic committed artifacts whose report hash binds the
frontend payload (the can't-drift contract), Markdown rendered solely from the typed
report with the signed-wording rules, and the thin CLI end to end — prod-refused,
reference-engine runs unpublishable, a full run -> publish flow on a tiny dataset."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmark_gfp
from lib.gfp.boundaries import (
    CuratedMotif,
    CuratedMotifEdge,
    CuratedMotifNode,
    CuratedVisualData,
    DatasetProvenance,
    DatasetStudySpec,
    FoldAssignment,
    StudyHighlightMetrics,
)
from lib.gfp.config import load_gfp_benchmark_config
from lib.gfp.publish import (
    REPORT_BASENAME,
    STUDY_JSON,
    CuratedRunPayload,
    load_run,
    publish_run,
    render_markdown,
    validate_publishable,
    validate_published_artifacts,
    write_run_artifacts,
)
from lib.gfp.report import (
    ArmDelta,
    ArmMetrics,
    HoldoutSummary,
    PairedDeltaInterval,
    ScopeComparison,
    StudyReport,
    TopKMetrics,
)

_SOURCE = "ibm-aml"
_TEST_CONFIG_YAML = """
seed: 1729
batch_size: 128
engine: {name: snapml, version: "1.17.2"}
edge_columns: [edge_id, dense_src, dense_dst, utc_epoch_s, usd_amount]
windows:
  global_window_s: 86400
  fan_window_s: 86400
  degree_window_s: 86400
  vertex_stats_window_s: 86400
  temporal_cycle_window_s: 86400
  simple_cycle_window_s: 86400
  scatter_gather_window_s: 21600
bins:
  fan: {lo: 2, hi: 30}
  degree: {lo: 2, hi: 30}
  scatter_gather: {lo: 2, hi: 30}
  temporal_cycle: {lo: 2, hi: 30}
  simple_cycle: {lo: 2, hi: 10}
simple_cycle_max_length: 10
vertex_stats:
  endpoints: [source_out, source_in, target_out, target_in]
  stats: [fan, degree, ratio, avg, sum, var, skew, kurtosis]
  raw_columns: [utc_epoch_s, usd_amount]
datasets:
  - {source: ibm-aml, graph_context: full, target_quota: null}
sampling: {node_hash_fractions: ["1/4", "1/3", "1/2"]}
folds: {train: "3/5", calibration: "1/5", holdout: "1/5"}
target_quotas: {train: 600000, calibration: 200000, holdout: 200000}
paths: {data_dir: .local/aml_data, output_dir: .local/gfp-study}
usd_rates: {us dollar: "1"}
"""


def _test_config(tmp_path: Path) -> tuple[Path, object]:
    path = tmp_path / "gfp-benchmark.yaml"
    path.write_text(_TEST_CONFIG_YAML, encoding="utf-8")
    return path, load_gfp_benchmark_config(path)


def _arm(arm: str, scope: str, pr_auc: float) -> ArmMetrics:
    return ArmMetrics(
        dataset_source=_SOURCE,
        arm=arm,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        holdout=HoldoutSummary(positives=8, negatives=792, illicit_ratio=0.01),
        pr_auc=pr_auc,
        pr_auc_normalized=pr_auc / 0.01,
        roc_auc=0.9,
        brier=0.01,
        ece=0.02,
        top_k=(TopKMetrics(fraction=0.001, precision=0.5, recall=0.1, captured_positives=1),),
        minority_f1=0.4,
        minority_f1_threshold=0.7,
    )


def _delta(from_arm: str, to_arm: str, scope: str, value: float, lower: float) -> ArmDelta:
    return ArmDelta(
        dataset_source=_SOURCE,
        from_arm=from_arm,  # type: ignore[arg-type]
        to_arm=to_arm,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        pr_auc_delta=value,
        interval=PairedDeltaInterval(
            lower=lower, upper=value + 0.02, replicates=200, holdout_subset_cap=250000
        ),
    )


def _report(
    *,
    engine: str = "snapml",
    lift: float = 0.05,
    interval_lower: float = 0.01,
    isolation_c: float = 0.02,
    notes: tuple[str, ...] = ("batch-causal disclosure",),
) -> StudyReport:
    metrics = (
        _arm("A", "shared", 0.20),
        _arm("B", "global", 0.22),
        _arm("C", "global", 0.20 + lift),
        _arm("B", "per_tenant", 0.21),
        _arm("C", "per_tenant", 0.20 + lift - isolation_c),
    )
    deltas = tuple(
        _delta(from_arm, to_arm, scope, lift, interval_lower)
        for scope in ("global", "per_tenant")
        for from_arm, to_arm in (("A", "B"), ("B", "C"), ("A", "C"))
    )
    comparison = ScopeComparison(
        dataset_source=_SOURCE,
        isolation_delta_b=0.01,
        isolation_delta_c=isolation_c,
        lost_graph_lift=isolation_c,
        retained_graph_lift=0.6 if lift > 0 else None,
        retained_lift_note=None if lift > 0 else "global Arm-C lift is not positive",
    )
    return StudyReport(
        run_id="gfp-0011223344556677",
        engine_name=engine,  # type: ignore[arg-type]
        engine_version="1.17.2" if engine == "snapml" else "builtin",
        library_versions={"numpy": "2.0.0"},
        config_sha256="c" * 64,
        seed=1729,
        datasets=(
            DatasetProvenance(
                spec=DatasetStudySpec(
                    source=_SOURCE, variant="HI-Small_Trans.csv", graph_context="full"
                ),
                file_sha256="b" * 64,
                source_row_count=100,
                servable_row_count=100,
                context_edge_count=100,
                target_count=100,
                illicit_ratio=0.01,
                fold_assignment=FoldAssignment(
                    fractions=("3/5", "1/5", "1/5"),
                    boundary_epochs_s=(1, 2),
                    fold_sizes=(60, 20, 20),
                    fold_positive_counts=(2, 1, 1),
                ),
                fold_target_counts=(60, 20, 20),
                fold_target_positive_counts=(2, 1, 1),
            ),
        ),
        arm_feature_counts={"A": 19, "B": 187, "C": 254},
        graph_feature_names=("gfp_fan_in_ge_2_lt_3", "gfp_scatter_gather_ge_2_lt_3"),
        metrics=metrics,
        deltas=deltas,
        comparisons=(comparison,),
        serving_eligible=False,
        notes=notes,
    )


def _motif(typology: str, owners: tuple[int, ...]) -> CuratedMotif:
    nodes = (
        CuratedMotifNode(node_id="node-01", agency_index=owners[0]),
        CuratedMotifNode(node_id="node-02", agency_index=owners[-1]),
    )
    edges = tuple(
        CuratedMotifEdge(
            edge_id=f"edge-{position + 1:02d}",
            source_node_id="node-01" if position % 2 == 0 else "node-02",
            target_node_id="node-02" if position % 2 == 0 else "node-01",
            time_offset_s=position * 60,
            amount_band="1k-10k",
            owner_agency_index=owner,
        )
        for position, owner in enumerate(owners)
    )
    return CuratedMotif(
        motif_id=f"{typology}-abc123",
        typology=typology,  # type: ignore[arg-type]
        nodes=nodes,
        edges=edges,
        servable=len(set(owners)) == 1,
    )


def _payload(*, missing: tuple[str, ...] = ()) -> CuratedRunPayload:
    motifs = (
        _motif("scatter_gather", (0,)),
        _motif("intra_tenant_cycle", (0, 0)),
        _motif("cross_tenant_cycle", (0, 1)),
    )
    if missing:
        motifs = tuple(m for m in motifs if m.typology not in missing)
    return CuratedRunPayload(
        agency_names=("Agency One", "Agency Two", "Agency Three"),
        missing_typologies=missing,
        motifs=motifs,
    )


def test_run_artifacts_round_trip(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    write_run_artifacts(run_dir, _report(), _payload())
    assert (run_dir / STUDY_JSON).is_file()
    assert '"servingEligible": false' in (run_dir / STUDY_JSON).read_text(encoding="utf-8")
    report, payload = load_run(run_dir)
    assert report == _report()
    assert payload == _payload()
    with pytest.raises(FileNotFoundError, match="missing"):
        load_run(tmp_path / "absent")


def test_validate_publishable_gates(tmp_path: Path) -> None:
    _, config = _test_config(tmp_path)
    validate_publishable(_report(), _payload(), config)
    with pytest.raises(ValueError, match="engine 'reference' can never be published"):
        validate_publishable(_report(engine="reference"), _payload(), config)
    with pytest.raises(ValueError, match="no real 'cross_tenant_cycle' motif"):
        validate_publishable(_report(), _payload(missing=("cross_tenant_cycle",)), config)
    incomplete = _report()
    incomplete = incomplete.model_copy(update={"metrics": incomplete.metrics[:-1]})
    with pytest.raises(ValueError, match="missing the Arm C \\(per_tenant\\) evaluation"):
        validate_publishable(incomplete, _payload(), config)
    no_deltas = _report().model_copy(update={"deltas": ()})
    with pytest.raises(ValueError, match="missing the A->B \\(global\\) delta"):
        validate_publishable(no_deltas, _payload(), config)
    no_comparison = _report().model_copy(update={"comparisons": ()})
    with pytest.raises(ValueError, match="missing its isolation comparison"):
        validate_publishable(no_comparison, _payload(), config)


def test_publish_binds_report_hash_and_detects_drift(tmp_path: Path) -> None:
    _, config = _test_config(tmp_path)
    run_dir = tmp_path / "run"
    write_run_artifacts(run_dir, _report(), _payload())
    docs_dir = tmp_path / "docs"
    frontend_json = tmp_path / "frontend" / "gfp-tenant-isolation-study.json"
    result = publish_run(
        run_dir, config=config, docs_dir=docs_dir, frontend_json_path=frontend_json
    )
    assert result.report_json_path.name == f"{REPORT_BASENAME}.json"
    assert result.report_markdown_path.is_file()
    committed = json.loads(frontend_json.read_text(encoding="utf-8"))
    assert committed["reportSha256"] == result.report_sha256
    assert committed["agencyNames"] == ["Agency One", "Agency Two", "Agency Three"]
    # The hero metrics are projected onto the one committed frontend artifact (Phase 7).
    assert committed["metrics"]["datasetSource"] == _SOURCE
    assert committed["metrics"]["armCPrAuc"] == 0.25  # _report lift=0.05 -> Arm C global = 0.25
    assert committed["metrics"]["armAToCLift"] == 0.05
    assert committed["metrics"]["isolationDeltaC"] == 0.02
    validate_published_artifacts(result.report_json_path, frontend_json)
    tampered = result.report_json_path.read_text(encoding="utf-8").replace("0.2", "0.3")
    result.report_json_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(ValueError, match="artifacts drifted"):
        validate_published_artifacts(result.report_json_path, frontend_json)


def test_publish_redaction_fails_closed(tmp_path: Path) -> None:
    _, config = _test_config(tmp_path)
    run_dir = tmp_path / "run"
    poisoned = _report(notes=("wrote scratch to /Users/someone/output",))
    write_run_artifacts(run_dir, poisoned, _payload())
    with pytest.raises(ValueError, match="redaction scan failed"):
        publish_run(
            run_dir,
            config=config,
            docs_dir=tmp_path / "docs",
            frontend_json_path=tmp_path / "frontend.json",
        )


def test_markdown_wording_follows_the_signed_values() -> None:
    payload = _payload()
    visual = CuratedVisualData(
        report_sha256="a" * 64,
        metrics=StudyHighlightMetrics(
            dataset_source=_SOURCE,
            arm_a_pr_auc=0.20,
            arm_c_pr_auc=0.25,
            arm_c_pr_auc_normalized=25.0,
            arm_a_to_c_lift=0.05,
            arm_a_to_c_ci_lower=0.01,
            arm_a_to_c_ci_upper=0.07,
            isolation_delta_c=0.02,
        ),
        agency_names=payload.agency_names,
        motifs=payload.motifs,
    )
    positive = render_markdown(_report(lift=0.05, interval_lower=0.01), visual)
    assert "moved holdout PR-AUC from 0.2000 to 0.2500" in positive
    assert "cost of isolation" in positive  # isolation_c = +0.02
    assert "ADR-017" in positive
    assert "| ibm-aml | full |" in positive
    neutral = render_markdown(_report(lift=0.0, interval_lower=-0.03, isolation_c=-0.01), visual)
    assert "no significant holdout PR-AUC lift" in neutral
    assert "cost of isolation" not in neutral
    assert "isolation delta" in neutral


# ---------------------------------------------------------------------------- CLI
def _cli_rows() -> list[dict[str, str]]:
    def row(
        minute: int, src: tuple[str, str], dst: tuple[str, str], amount: str, label: str
    ) -> dict[str, str]:
        return {
            "Timestamp": f"2022/09/01 00:{minute:02d}",
            "From Bank": src[0],
            "Account": src[1],
            "To Bank": dst[0],
            "Account.1": dst[1],
            "Amount Paid": amount,
            "Payment Currency": "US Dollar",
            "Payment Format": "Wire",
            "Is Laundering": label,
        }

    motifs = [
        row(0, ("0", "A"), ("0", "M1"), "500.00", "0"),
        row(1, ("0", "A"), ("0", "M2"), "5000.00", "1"),
        row(2, ("0", "M1"), ("0", "B"), "50.00", "0"),
        row(3, ("0", "M2"), ("0", "B"), "20000.00", "0"),
        row(10, ("0", "X1"), ("0", "X2"), "200000.00", "0"),
        row(11, ("0", "X2"), ("0", "X3"), "300.00", "0"),
        row(12, ("0", "X3"), ("0", "X1"), "300.00", "0"),
        row(20, ("0", "Y1"), ("1", "Y2"), "300.00", "0"),
        row(21, ("1", "Y2"), ("2", "Y3"), "300.00", "0"),
        row(22, ("2", "Y3"), ("0", "Y1"), "300.00", "0"),
    ]
    fillers = [
        row(30 + i, ("0", f"F{i}"), ("1", f"G{i}"), "400.00", "1" if i % 3 == 0 else "0")
        for i in range(30)
    ]
    return motifs + fillers


def _write_cli_fixture(tmp_path: Path) -> Path:
    config_path, _ = _test_config(tmp_path)
    data_dir = tmp_path / ".local" / "aml_data"
    data_dir.mkdir(parents=True)
    rows = _cli_rows()
    with (data_dir / "HI-Small_Trans.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return config_path


def _stub_settings(environment: str = "dev") -> SimpleNamespace:
    return SimpleNamespace(environment=environment, investigation_history_max=100)


def _dev_settings() -> SimpleNamespace:
    return _stub_settings()


def test_cli_refuses_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_gfp, "get_settings", lambda: _stub_settings("prod"))
    assert benchmark_gfp.main(["run", "--engine", "fake"]) == 1
    assert benchmark_gfp.main(["publish", "--run", "gfp-x"]) == 1


def test_cli_run_then_publish_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_cli_fixture(tmp_path)
    monkeypatch.setattr(benchmark_gfp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(benchmark_gfp, "get_settings", _dev_settings)

    assert benchmark_gfp.main(["run", "--engine", "reference", "--config", str(config_path)]) == 0
    run_dirs = sorted((tmp_path / ".local" / "gfp-study").glob("gfp-*"))
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name
    report, payload = load_run(run_dirs[0])
    assert report.engine_name == "reference"
    assert report.serving_eligible is False
    assert payload.missing_typologies == ()
    assert {motif.typology for motif in payload.motifs} == {
        "scatter_gather",
        "intra_tenant_cycle",
        "cross_tenant_cycle",
    }

    # A reference-engine run can NEVER be promoted to a committed result.
    assert benchmark_gfp.main(["publish", "--run", run_id, "--config", str(config_path)]) == 1
    assert "can never be published" in capsys.readouterr().out

    # Forge the engine stamp (as a snapml x86-64 run would produce) and publish.
    study_path = run_dirs[0] / STUDY_JSON
    study = json.loads(study_path.read_text(encoding="utf-8"))
    study["engineName"] = "snapml"
    study["engineVersion"] = "1.17.2"
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert benchmark_gfp.main(["publish", "--run", run_id, "--config", str(config_path)]) == 0
    report_json = tmp_path / "docs" / "reference" / "benchmarks" / f"{REPORT_BASENAME}.json"
    frontend_json = tmp_path / "frontend" / "src" / "data" / "gfp-tenant-isolation-study.json"
    assert report_json.is_file()
    assert (tmp_path / "docs" / "reference" / "benchmarks" / f"{REPORT_BASENAME}.md").is_file()
    validate_published_artifacts(report_json, frontend_json)
    committed = json.loads(frontend_json.read_text(encoding="utf-8"))
    assert len(committed["motifs"]) == 3
    missing = ["publish", "--run", "gfp-missing", "--config", str(config_path)]
    assert benchmark_gfp.main(missing) == 1


def test_cli_run_fails_fast_on_absent_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path, _ = _test_config(tmp_path)
    monkeypatch.setattr(benchmark_gfp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(benchmark_gfp, "get_settings", _dev_settings)
    assert benchmark_gfp.main(["run", "--engine", "fake", "--config", str(config_path)]) == 1
    assert "never auto-downloads" in capsys.readouterr().out
