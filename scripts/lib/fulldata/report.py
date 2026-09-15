"""Summary: Aggregate run assembly plus JSON/Markdown reporting for full-data training.

Key classes:
- FullDataStudyReport: publishable reconciliation, provenance, timing, cost, and metrics.
- FrontendCandidateProjection: browser-safe aggregate candidate metrics.
- FrontendFullDataProjection: aggregate browser-safe report projection.

Key functions:
- evaluate_run: bind completed source stages into a reproducible run manifest.
- run_directory: resolve one local aggregate run directory.
- current_run_id: read the current local run pointer.
- frontend_projection: derive the aggregate browser-safe report.
- write_report: render canonical JSON and Markdown from the typed run.
- render_markdown: render tables and Mermaid metric comparisons.

Notes:
- Reports contain aggregate synthetic-data evidence only, never raw accounts or predictions.
"""

from __future__ import annotations

import itertools
import json
import os
import platform
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from lib.fulldata.config import FullDataConfig, FullDataDataset
from lib.fulldata.cost import load_cost_projection
from lib.fulldata.folds import load_fold_manifest
from lib.fulldata.ingest import ingested_scan, load_reconciliation
from lib.fulldata.manifest import (
    AccountOverlap,
    FullDataRunManifest,
    RunCost,
    SourceRunManifest,
    peak_rss_mib,
)
from lib.fulldata.timing import load_stage_measurements
from lib.fulldata.train import candidate_result_path, load_candidate_evaluation
from lib.gitio import run_git
from lib.study import atomic_write_model, atomic_write_text, canonical_json, derive_run_id

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)


class FullDataStudyReport(BaseModel):
    """Publishable typed full-data training report."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., description="Hash-derived stable run identity.")
    title: str = Field(..., description="Human-readable study title.")
    synthetic_data: bool = Field(..., description="True for the public synthetic IBM corpus.")
    manifest: FullDataRunManifest = Field(..., description="Complete aggregate run provenance.")


class FrontendCandidateProjection(BaseModel):
    """Browser-safe aggregate result for one source candidate."""

    model_config = _MODEL_CONFIG

    candidate: str = Field(..., description="Pre-registered candidate identity.")
    source: str = Field(..., description="IBM source identity.")
    source_rows: int = Field(..., ge=0, description="Rows read from the source.")
    usable_rows: int = Field(..., ge=0, description="Rows surviving usability rules.")
    training_rows: int = Field(..., ge=0, description="Rows in the training fold.")
    evaluation_rows: int = Field(..., ge=0, description="Rows in final holdout evaluation.")
    pr_auc: float = Field(..., ge=0.0, le=1.0, description="Candidate holdout PR-AUC.")
    baseline_pr_auc: float = Field(..., ge=0.0, le=1.0, description="LR baseline holdout PR-AUC.")
    recall_at_budget: float = Field(..., ge=0.0, le=1.0, description="Holdout alert recall.")
    gates_passed: bool = Field(..., description="Whether every shared model gate passed.")


class FrontendFullDataProjection(BaseModel):
    """Hash-bound aggregate projection consumed by a later frontend phase."""

    model_config = _MODEL_CONFIG

    report_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Full report hash.")
    run_id: str = Field(..., description="Source report run identity.")
    provenance: str = Field(..., description="Sample or measured provenance tag.")
    application_candidate: str = Field(..., description="Pre-registered app candidate.")
    candidates: tuple[FrontendCandidateProjection, ...] = Field(
        ..., min_length=1, description="Aggregate candidate metrics."
    )


def _overlap(
    config: FullDataConfig,
    left: FullDataDataset,
    right: FullDataDataset,
    repo_root: Path,
) -> AccountOverlap:
    """Count raw bank/account keys shared across two independently namespaced source files."""
    left_path = ingested_scan(config, left, repo_root)
    right_path = ingested_scan(config, right, repo_root)
    with duckdb.connect() as connection:
        row = connection.execute(
            """
            WITH left_accounts AS (
              SELECT DISTINCT split_part(account, ':', 2) || ':' ||
                split_part(account, ':', 3) AS key
              FROM (
                SELECT origin_account AS account FROM read_parquet(?, hive_partitioning=false)
                UNION ALL SELECT dest_account FROM read_parquet(?, hive_partitioning=false)
              )
            ), right_accounts AS (
              SELECT DISTINCT split_part(account, ':', 2) || ':' ||
                split_part(account, ':', 3) AS key
              FROM (
                SELECT origin_account AS account FROM read_parquet(?, hive_partitioning=false)
                UNION ALL SELECT dest_account FROM read_parquet(?, hive_partitioning=false)
              )
            )
            SELECT count(*) FROM left_accounts INNER JOIN right_accounts USING (key)
            """,
            [left_path, left_path, right_path, right_path],
        ).fetchone()
    if row is None:
        raise RuntimeError("DuckDB returned no overlap count")
    return AccountOverlap(
        left_source=left.source,
        right_source=right.source,
        overlapping_accounts=int(row[0]),
    )


def _completed_sources(
    config: FullDataConfig, repo_root: Path
) -> tuple[tuple[FullDataDataset, SourceRunManifest], ...]:
    completed = []
    for dataset in config.datasets:
        if not candidate_result_path(config, dataset, repo_root).is_file():
            continue
        completed.append(
            (
                dataset,
                SourceRunManifest(
                    candidate=dataset.candidate,
                    source=dataset.source,
                    reconciliation=load_reconciliation(config, dataset, repo_root),
                    folds=load_fold_manifest(config, dataset, repo_root),
                    evaluation=load_candidate_evaluation(config, dataset, repo_root),
                ),
            )
        )
    if not completed:
        raise FileNotFoundError("no completed full-data candidate results are available")
    return tuple(completed)


def run_directory(config: FullDataConfig, repo_root: Path, run_id: str) -> Path:
    """Resolve one local aggregate run directory."""
    return repo_root / config.paths.work_dir / "runs" / run_id


def current_run_id(config: FullDataConfig, repo_root: Path) -> str:
    """Read the current local run pointer."""
    return (repo_root / config.paths.work_dir / "current-run.txt").read_text("utf-8").strip()


def evaluate_run(config: FullDataConfig, repo_root: Path) -> FullDataRunManifest:
    """Bind all completed source results into a hash-derived aggregate run manifest."""
    completed = _completed_sources(config, repo_root)
    sources = tuple(item[1] for item in completed)
    full = len(completed) == len(config.datasets) and all(
        source.reconciliation.limited_rows is None
        and source.reconciliation.source_rows == dataset.rows_expected
        for dataset, source in completed
    )
    commit = run_git(["rev-parse", "--short=12", "HEAD"]).strip() or "unknown-revision"
    identity = json.dumps(
        {
            "configSha256": config.config_sha256,
            "commit": commit,
            "models": [source.evaluation.model_sha256 for source in sources if source.evaluation],
        },
        sort_keys=True,
    )
    run_id = derive_run_id("fulldata", identity)
    overlaps = tuple(
        _overlap(config, left[0], right[0], repo_root)
        for left, right in itertools.combinations(completed, 2)
    )
    measurements = tuple(
        (dataset, load_stage_measurements(config, dataset, repo_root))
        for dataset, _source in completed
    )
    stage_durations = {
        f"{stage}:{dataset.source}": seconds
        for dataset, ledger in measurements
        for stage, seconds in ledger.durations_seconds.items()
    }
    stage_memory = {
        f"{stage}:{dataset.source}": memory
        for dataset, ledger in measurements
        for stage, memory in ledger.peak_rss_mib.items()
    }
    for source in sources:
        if source.evaluation is not None:
            stage_durations[f"train:{source.source}"] = source.evaluation.training_seconds
            stage_memory[f"train:{source.source}"] = max(
                stage_memory.get(f"train:{source.source}", 0.0),
                source.evaluation.training_peak_rss_mib,
            )
    ended = datetime.now(UTC)
    total_duration = sum(stage_durations.values())
    started = ended - timedelta(seconds=total_duration)
    vm_sku = os.environ.get("FRAUDLENS_VM_SKU", f"local-{platform.machine()}")
    evaluator_rss = peak_rss_mib()
    stage_memory["evaluate"] = evaluator_rss
    projection = load_cost_projection(config, repo_root)
    manifest = FullDataRunManifest(
        run_id=run_id,
        config_sha256=config.config_sha256,
        commit=commit,
        provenance="measured" if full else "sample",
        application_candidate=config.application_candidate,
        started_at=started.isoformat(),
        completed_at=ended.isoformat(),
        duration_seconds=total_duration,
        peak_rss_mib=max(stage_memory.values()),
        vm_sku=vm_sku,
        library_versions={name: version(name) for name in ("duckdb", "pyarrow", "xgboost")},
        sources=sources,
        cross_file_overlap=overlaps,
        stage_durations_seconds=stage_durations,
        stage_peak_rss_mib=stage_memory,
        cost=RunCost(
            allocation=config.budget.allocation,
            projected_usd=(
                float(projection.projection.projected_cost_usd) if projection is not None else None
            ),
            actual_usd=0.0 if vm_sku.startswith("local-") else None,
        ),
    )
    directory = run_directory(config, repo_root, run_id)
    atomic_write_model(directory / "manifest.json", manifest)
    atomic_write_text(repo_root / config.paths.work_dir / "current-run.txt", run_id + "\n")
    return manifest


def _report_from_manifest(manifest: FullDataRunManifest) -> FullDataStudyReport:
    return FullDataStudyReport(
        run_id=manifest.run_id,
        title="IBM AML full-data temporal training study",
        synthetic_data=True,
        manifest=manifest,
    )


def frontend_projection(
    report: FullDataStudyReport, report_sha256: str
) -> FrontendFullDataProjection:
    """Project aggregate source counts and model metrics for browser use."""
    candidates = []
    for source in report.manifest.sources:
        if source.evaluation is None:
            continue
        candidates.append(
            FrontendCandidateProjection(
                candidate=source.candidate,
                source=source.source,
                source_rows=source.reconciliation.source_rows,
                usable_rows=source.reconciliation.usable_rows,
                training_rows=source.folds.counts.train.rows,
                evaluation_rows=source.folds.counts.holdout.rows,
                pr_auc=source.evaluation.metrics.pr_auc,
                baseline_pr_auc=source.evaluation.baseline_pr_auc,
                recall_at_budget=source.evaluation.metrics.recall_at_budget,
                gates_passed=source.evaluation.gates.passed,
            )
        )
    return FrontendFullDataProjection(
        report_sha256=report_sha256,
        run_id=report.run_id,
        provenance=report.manifest.provenance,
        application_candidate=report.manifest.application_candidate,
        candidates=tuple(candidates),
    )


def render_markdown(report: FullDataStudyReport) -> str:
    """Render reconciliation, evaluation, timing/cost, and Mermaid comparisons."""
    projected = report.manifest.cost.projected_usd
    actual = report.manifest.cost.actual_usd
    lines = [
        f"# {report.title}",
        "",
        f"- Run: `{report.run_id}` ({report.manifest.provenance})",
        f"- Config SHA-256: `{report.manifest.config_sha256}`",
        f"- Commit: `{report.manifest.commit}`",
        f"- Application candidate (pre-registered): `{report.manifest.application_candidate}`",
        "",
        "## Reconciliation and temporal evaluation",
        "",
        "| Candidate | Input SHA | Model SHA | Source rows | Usable | Rejected | Train (prev.) | "
        "Tuning (prev.) | Calibration (prev.) | Holdout (prev.) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    evaluations = []
    for source in report.manifest.sources:
        counts = source.folds.counts
        lines.append(
            f"| {source.candidate} | `{source.reconciliation.input_sha256}` | "
            f"`{source.evaluation.model_sha256 if source.evaluation else 'pending'}` | "
            f"{source.reconciliation.source_rows} | "
            f"{source.reconciliation.usable_rows} | {source.reconciliation.rejected.total} | "
            f"{counts.train.rows} ({counts.train.prevalence:.8f}) | "
            f"{counts.tuning.rows} ({counts.tuning.prevalence:.8f}) | "
            f"{counts.calibration.rows} ({counts.calibration.prevalence:.8f}) | "
            f"{counts.holdout.rows} ({counts.holdout.prevalence:.8f}) |"
        )
        if source.evaluation is not None:
            evaluations.append(source.evaluation)
    lines += [
        "",
        "## Candidate versus capped logistic baseline",
        "",
        "| Candidate | PR-AUC | Baseline PR-AUC | Recall@budget | Gates | Threshold source |",
        "|---|---:|---:|---:|---|---|",
    ]
    for result in evaluations:
        lines.append(
            f"| {result.candidate} | {result.metrics.pr_auc:.6f} | "
            f"{result.baseline_pr_auc:.6f} | {result.metrics.recall_at_budget:.6f} | "
            f"{'pass' if result.gates.passed else 'fail'} | {result.threshold_source} |"
        )
    labels = ", ".join(f'"{result.candidate}"' for result in evaluations)
    values = ", ".join(f"{result.metrics.pr_auc:.6f}" for result in evaluations)
    baseline = ", ".join(f"{result.baseline_pr_auc:.6f}" for result in evaluations)
    lines += [
        "",
        "```mermaid",
        "xychart-beta",
        f"    x-axis [{labels}]",
        '    y-axis "PR-AUC" 0 --> 1',
        f"    bar [{values}]",
        f"    line [{baseline}]",
        "```",
        "",
        "## Runtime and cost",
        "",
        "| Stage | Seconds | Peak RSS MiB |",
        "|---|---:|---:|",
        *(
            f"| {stage} | {seconds:.3f} | "
            f"{report.manifest.stage_peak_rss_mib.get(stage, 0.0):.2f} |"
            for stage, seconds in sorted(report.manifest.stage_durations_seconds.items())
        ),
        "",
        f"Total recorded stage time: {report.manifest.duration_seconds:.3f} seconds.",
        "",
        f"Peak RSS: {report.manifest.peak_rss_mib:.2f} MiB. Host: "
        f"`{report.manifest.vm_sku}`. Projected cost: "
        f"{projected if projected is not None else 'not set'}. "
        f"Actual cost: {actual if actual is not None else 'pending'}.",
        "",
        "Cross-file overlap is reported in the JSON manifest before treating source files as "
        "independent evidence. Dataset rows are transactions, not SAR cases.",
    ]
    return "\n".join(lines) + "\n"


def write_report(config: FullDataConfig, repo_root: Path) -> FullDataStudyReport:
    """Load the current manifest and write canonical local JSON plus Markdown."""
    run_id = current_run_id(config, repo_root)
    directory = run_directory(config, repo_root, run_id)
    manifest = FullDataRunManifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    report = _report_from_manifest(manifest)
    atomic_write_text(directory / "study.json", canonical_json(report))
    atomic_write_text(directory / "study.md", render_markdown(report))
    return report
