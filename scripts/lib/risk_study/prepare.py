"""Summary: Reuse the archived, hash-verified original experiment's local preprocessing.

Key classes:
- (none)

Key functions:
- prepare_inputs: recreate synthetic feature inputs without retraining or changing history.

Notes:
- The historical source is retained byte-for-byte as an evidence attachment, not rewritten.
- Whole-source materialization is followed by the canonical account-whole evaluation split.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from lib.fulldata.config import load_fulldata_config
from lib.risk_study.inputs import digest
from lib.risk_study.models import StudyConfig, StudyRun


def _replay_module(root: Path, config: StudyConfig) -> ModuleType:
    """Install the verified historical attachment at its original path without overwrites."""
    archive = root / config.replay_source
    if digest(archive) != config.replay_sha256:
        raise ValueError("archived replay source hash mismatch")
    target = root / ".local/risk-blend-study/run.py"
    if target.exists() and digest(target) != config.replay_sha256:
        raise ValueError("existing local replay differs; refusing to overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(archive.read_bytes())
    module_name = "fraudlens_risk_study_preprocessing"
    spec = importlib.util.spec_from_file_location(module_name, target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def prepare_inputs(root: Path, config: StudyConfig, runs: list[StudyRun]) -> None:
    """Recreate missing inputs using frozen CSVs and exact original preprocessing semantics."""
    replay = _replay_module(root, config)
    full_config = load_fulldata_config()
    reference = json.loads(
        (root / "docs/reference/benchmarks/ibm-full-data-training.json").read_text()
    )
    completed: set[str] = set()
    for run in runs:
        if run.work_dir in completed:
            continue
        source = next(
            s for s in reference["manifest"]["sources"] if s["candidate"] == run.candidate
        )
        if run.split == "account_whole":
            # Pre-epoch sentinels select every IBM row; no historical fold is relabelled.
            # The actual account-whole allocation occurs in inputs.account_evaluation.
            counts = source["folds"]["counts"].values()
            total, positives = sum(x["rows"] for x in counts), sum(x["positives"] for x in counts)
            source["folds"]["boundary_epochs"] = [-1, -1, -1]
            source["folds"]["counts"] = {"holdout": {"rows": total, "positives": positives}}
        destination = root / run.work_dir
        destination.mkdir(parents=True, exist_ok=True)
        replay.prepare(run.candidate, full_config, source, destination)
        completed.add(run.work_dir)
