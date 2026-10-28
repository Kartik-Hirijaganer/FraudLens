"""Calibration-first persistence, reusable reports, CLI selection, and frozen replay admission."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from risk_study_fakes import study_fixture

import evaluate_risk_policy
from lib.risk_study import evaluate, prepare
from lib.risk_study.inputs import digest
from lib.risk_study.metrics import at_budget


@pytest.mark.parametrize("training_end", [None, 6 * 86400])
def test_selection_is_saved_before_holdout_and_pattern_cache_is_bound(
    sandbox, monkeypatch, training_end
):
    config, run, _ = study_fixture(sandbox)
    run = run.model_copy(update={"training_end_epoch": training_end})
    calls = []

    def count_metric(*args):
        if len(calls) == len(config.weights):
            selection = json.loads(
                (sandbox / config.output_dir / run.name / "calibration-selection.json").read_text()
            )
            assert selection["holdout"] == {}
            assert selection["selected_weight"] == config.policy.model_weight
        calls.append(1)
        return at_budget(*args)

    monkeypatch.setattr(evaluate, "at_budget", count_metric)

    def patterns(_root, _run, cohort):
        return {
            int(cohort.row_id[i]): () for i in np.flatnonzero(cohort.holdout & (cohort.label == 1))
        }

    monkeypatch.setattr(evaluate, "positive_patterns", patterns)
    report = evaluate.evaluate_run(sandbox, config, run)
    assert report.exploratory and report.selected_weight == 0.7
    assert report.holdout["model-only"]["exchange_against_default"]["lost_positives"] == 0
    assert report.holdout["controls"]["raw_and_normalized_model_only_same_selection"]
    first = report.model_dump()
    monkeypatch.setattr(evaluate, "at_budget", at_budget)
    monkeypatch.setattr(evaluate, "positive_patterns", lambda *_: pytest.fail("stale cache reused"))
    assert evaluate.evaluate_run(sandbox, config, run).model_dump() == first
    marker = sandbox / config.output_dir / run.name / "positive-patterns.sha256"
    marker.write_text("stale")
    monkeypatch.setattr(evaluate, "positive_patterns", patterns)
    assert evaluate.evaluate_run(sandbox, config, run).model_dump() == first


def test_cli_selects_runs_prepares_without_evaluating_and_rejects_unknown(sandbox, monkeypatch):
    config, run, _ = study_fixture(sandbox)
    path = sandbox / "study.yaml"
    path.write_text(config.model_dump_json())
    monkeypatch.setattr(evaluate_risk_policy, "ROOT", sandbox)
    calls = []
    monkeypatch.setattr(
        evaluate_risk_policy, "evaluate_run", lambda root, cfg, r: calls.append(r.name)
    )
    monkeypatch.setattr(sys, "argv", ["evaluate", "--config", str(path)])
    assert evaluate_risk_policy.main() == 0 and calls == [run.name]
    monkeypatch.setattr(
        evaluate_risk_policy, "prepare_inputs", lambda root, cfg, runs: calls.append("prepare")
    )
    monkeypatch.setattr(
        sys, "argv", ["evaluate", "--config", str(path), "--prepare", "--run", run.name]
    )
    assert evaluate_risk_policy.main() == 0 and calls == [run.name, "prepare"]
    monkeypatch.setattr(sys, "argv", ["evaluate", "--config", str(path), "--run", "missing"])
    with pytest.raises(SystemExit) as error:
        evaluate_risk_policy.main()
    assert error.value.code == 2


def test_verified_replay_source_is_preserved_and_mismatch_is_rejected(sandbox):
    config, _, _ = study_fixture(sandbox)
    archive = sandbox / "replay.txt"
    archive.write_text("EVIDENCE = 1\n")
    config = config.model_copy(
        update={"replay_source": "replay.txt", "replay_sha256": digest(archive)}
    )
    assert prepare._replay_module(sandbox, config).EVIDENCE == 1
    assert prepare._replay_module(sandbox, config).EVIDENCE == 1
    target = sandbox / ".local/risk-blend-study/run.py"
    target.write_text("EVIDENCE = 2\n")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare._replay_module(sandbox, config)
    archive.write_text("changed\n")
    with pytest.raises(ValueError, match="archived replay source"):
        prepare._replay_module(sandbox, config)


def test_preparation_materializes_all_accounts_once_without_changing_historical_reference(
    sandbox, monkeypatch
):
    config, run, _ = study_fixture(sandbox)
    counts = {
        "train": {"rows": 6, "positives": 1},
        "calibration": {"rows": 2, "positives": 1},
        "holdout": {"rows": 2, "positives": 0},
    }
    reference = {
        "manifest": {
            "sources": [
                {
                    "candidate": run.candidate,
                    "folds": {"boundary_epochs": [1, 2, 3], "counts": counts},
                }
            ]
        }
    }
    path = sandbox / "docs/reference/benchmarks/ibm-full-data-training.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(reference))
    seen = []
    monkeypatch.setattr(
        prepare,
        "_replay_module",
        lambda *_: SimpleNamespace(
            prepare=lambda candidate, cfg, source, destination: seen.append(source)
        ),
    )
    whole = run.model_copy(update={"split": "account_whole"})
    prepare.prepare_inputs(sandbox, config, [whole, whole])
    assert len(seen) == 1 and seen[0]["folds"]["counts"] == {
        "holdout": {"rows": 10, "positives": 2}
    }
    assert seen[0]["folds"]["boundary_epochs"] == [-1, -1, -1]
    assert json.loads(path.read_text()) == reference
    prepare.prepare_inputs(sandbox, config, [run])
    assert seen[-1]["folds"] == reference["manifest"]["sources"][0]["folds"]
