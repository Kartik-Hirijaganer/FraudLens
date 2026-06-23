"""Summary: The model retrain Job (plan §16 Phase 10, §9.4, §10.5/§10.5.1). It is the scheduled
(monthly) / manually-triggered Container Apps Job that turns MATURED reviewed labels into a new
CANDIDATE — never touching the active pointer (promotion stays human-gated). Eligibility is gated
first: only matured reviewed `training_labels` count, and they must clear the configured total +
per-class thresholds (else `insufficient_matured_labels`, §9.4). Because FraudLens ships no real
IEEE-CIS and matured labelled volume in a demo is tiny, the candidate is TRAINED on the same
deterministic synthetic IEEE-CIS-shaped dataset Phase 5 uses (reusing `train_candidate` — no
duplicated training logic), while the matured labels drive eligibility and the immutable, PHI-free
dataset manifest (label window + aggregate counts — never PHI or `agency_id`, ADR-015). The
candidate is gated against the §10.5.1 metric gates AND the active model (no regression) AND the
per-tenant slice gate (§9.4), recorded overall + per-slice in `model_evaluations`, then registered
as a CANDIDATE `model_versions` row (+ training run + job) — the active deployment pointer remains
untouched.

Key classes:
- (none)

Key functions:
- read_active_pr_auc: the current active model's recorded PR-AUC (the regression baseline).
- tenant_slice_pr_aucs: per-tenant slice PR-AUCs over deterministic holdout partitions (§9.4).
- register_retrained_candidate: idempotently register the dataset/run/version/evaluation/job rows.
- main: CLI — eligibility → train → gate (overall + active + per-tenant) → register candidate.

Notes:
- Candidate-only by construction: the active/canary `model_deployments` pointer is never written
  here; the API's shadow/approve/canary/rollback flow promotes it (human-gated, plan §10.5).
- Registration is idempotent by version label (label = hash of feature-spec + seed + matured-label
  counts), so re-running with the same labels is a no-op (mirrors `train_model`).
- Exit codes: 0 ok, 1 fatal (no DB / unexpected failure), 2 gates failed (candidate still
  registered for audit), 3 insufficient matured labels (nothing trained).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import numpy as np
import xgboost as xgb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    JobExecution,
    JobStatus,
    JobType,
    ModelEvaluation,
    ModelTrainingRun,
    ModelTrigger,
    ModelVersion,
    ModelVersionStatus,
    TrainingDataset,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository
from fraudlens_backend.db.repositories.model_lifecycle import LabelCounts, labels_eligible
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.settings import get_settings
from fraudlens_ml.scoring import (
    GateCheck,
    GateReport,
    ModelGates,
    average_precision,
    current_feature_spec,
    evaluate_gates,
    evaluate_tenant_slices,
    save_artifact,
)
from lib.synthetic_fraud import DataSplit, generate_dataset, split_dataset
from train_model import TrainedCandidate, _artifacts_root, train_candidate

# Reuse Phase 5's deterministic seed/size so the candidate reliably clears the gates (and, trained
# on the identical synthetic data as the fixture active model, never trips the regression gate).
_SEED = 1729
_TRAIN_ROWS = 16000


def read_active_pr_auc(version: ModelVersion | None) -> float | None:
    """Return the active model's recorded holdout PR-AUC (the regression baseline), or None."""
    if version is None:
        return None
    value = (version.metrics or {}).get("pr_auc")
    return float(value) if isinstance(value, (int, float)) else None


def tenant_slice_pr_aucs(
    split: DataSplit, trained: TrainedCandidate, *, slices: int
) -> dict[str, float]:
    """Compute per-tenant slice PR-AUCs over deterministic holdout partitions (plan §9.4).

    FraudLens trains on synthetic data, so the per-tenant slices are deterministic contiguous
    partitions of the (already permuted) holdout — a tenant-anonymous stand-in for per-agency
    slices that still exercises the §9.4 "no slice may regress" gate. Empty partitions are skipped.
    """
    margin = np.asarray(trained.booster.predict(xgb.DMatrix(split.x_holdout), output_margin=True))
    probability = trained.calibration.apply(margin)
    partitions = np.array_split(np.arange(split.y_holdout.shape[0]), slices)
    return {
        f"slice-{index}": average_precision(split.y_holdout[idx], probability[idx])
        for index, idx in enumerate(partitions)
        if idx.size > 0
    }


def _version_label(seed: int, counts: LabelCounts) -> str:
    """Return a deterministic retrain version label (same feature-spec + seed + counts → same)."""
    spec = current_feature_spec()
    digest = hashlib.sha256(
        json.dumps(
            {
                "features": spec.features,
                "seed": seed,
                "labels": {"total": counts.total, "pos": counts.positives, "neg": counts.negatives},
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"retrain-fs{spec.version}-{digest[:10]}"


def _dataset_hash(seed: int, counts: LabelCounts, label_window: str) -> str:
    """Return the content hash for the PHI-free synthetic+labels dataset manifest (reproducible)."""
    spec = current_feature_spec()
    return hashlib.sha256(
        json.dumps(
            {
                "source": "synthetic",
                "features": spec.features,
                "seed": seed,
                "labelWindow": label_window,
                "labels": {"total": counts.total, "pos": counts.positives, "neg": counts.negatives},
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _metrics_payload(
    trained: TrainedCandidate,
    report: GateReport,
    slice_checks: list[GateCheck],
    slice_pr_aucs: dict[str, float],
) -> dict[str, object]:
    """Build the PHI-free metrics map persisted on the candidate version + training run."""
    payload: dict[str, object] = dict(trained.metrics.model_dump())
    payload["baseline_pr_auc"] = trained.baseline_pr_auc
    payload["tenantSlices"] = slice_pr_aucs
    payload["gates_passed"] = report.passed and all(check.passed for check in slice_checks)
    return payload


async def register_retrained_candidate(  # noqa: PLR0913 - registers several rows (keyword-only).
    session: AsyncSession,
    trained: TrainedCandidate,
    report: GateReport,
    slice_checks: list[GateCheck],
    slice_pr_aucs: dict[str, float],
    *,
    version_label: str,
    artifact_uri: str,
    trigger: ModelTrigger,
    counts: LabelCounts,
    label_window: str,
    active_version_id: uuid.UUID | None,
    seed: int,
    rows: int,
) -> tuple[uuid.UUID, bool]:
    """Idempotently register the dataset/run/version/evaluation/job rows; return (id, passed)."""
    metrics_payload = _metrics_payload(trained, report, slice_checks, slice_pr_aucs)
    passed = bool(metrics_payload["gates_passed"])
    existing = (
        await session.execute(
            select(ModelVersion).where(ModelVersion.version_label == version_label)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id, passed

    spec = current_feature_spec()
    dataset = TrainingDataset(
        snapshot_query={
            "source": "synthetic",
            "seed": seed,
            "rows": rows,
            "maturedLabels": {
                "total": counts.total,
                "positives": counts.positives,
                "negatives": counts.negatives,
            },
        },
        label_window=label_window,
        row_count=rows,
        feature_spec=spec.model_dump(),
        content_hash=_dataset_hash(seed, counts, label_window),
    )
    session.add(dataset)
    await session.flush()
    training_run = ModelTrainingRun(
        trigger=trigger,
        dataset_id=dataset.id,
        status=JobStatus.SUCCEEDED,
        params={"seed": seed, "rows": rows, "source": "synthetic"},
        metrics=metrics_payload,
        artifact_uri=artifact_uri,
    )
    session.add(training_run)
    await session.flush()
    version = ModelVersion(
        version_label=version_label,
        training_run_id=training_run.id,
        artifact_uri=artifact_uri,
        feature_spec=spec.model_dump(),
        metrics=metrics_payload,
        status=ModelVersionStatus.CANDIDATE,
        notes="Retrained candidate (Phase 10); promotion is human-gated (shadow→canary→active).",
    )
    session.add(version)
    await session.flush()
    session.add(
        ModelEvaluation(
            model_version_id=version.id,
            baseline_version_id=active_version_id,
            metrics={
                "checks": [check.model_dump() for check in report.checks],
                "tenantSliceChecks": [check.model_dump() for check in slice_checks],
                **metrics_payload,
            },
            passed=passed,
        )
    )
    session.add(
        JobExecution(
            agency_id=None,
            job_type=JobType.RETRAIN,
            status=JobStatus.SUCCEEDED,
            payload={"version_label": version_label, "trigger": trigger.value},
            result={"gates_passed": passed, "pr_auc": trained.metrics.pr_auc},
            attempts=1,
        )
    )
    await session.flush()
    return version.id, passed


def _print_report(
    label: str, report: GateReport, slice_checks: list[GateCheck], passed: bool
) -> None:
    """Print a concise PHI-free retrain summary (verdict + key metrics)."""
    metrics = report.metrics
    active = "none" if report.active_pr_auc is None else f"{report.active_pr_auc:.3f}"
    print(
        f"retrain OK (candidate '{label}'): gates_passed={passed} "
        f"pr_auc={metrics.pr_auc:.3f} baseline={report.baseline_pr_auc:.3f} "
        f"active={active} slices={len(slice_checks)}"
    )


async def _amain(trigger: ModelTrigger, rows: int, seed: int) -> int:
    """Gate eligibility, train + gate a candidate from matured labels, register it."""
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("retrain failed: DATABASE_URL is not configured")
        return 1
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            now = datetime.now(UTC)
            lifecycle = ModelLifecycleRepository(session)
            counts = await lifecycle.matured_label_counts(as_of=now)
            if not labels_eligible(
                counts,
                min_total=settings.retrain_min_labels_total,
                min_per_class=settings.retrain_min_labels_per_class,
            ):
                print(
                    f"retrain skipped: insufficient matured labels "
                    f"(total={counts.total}, pos={counts.positives}, neg={counts.negatives})"
                )
                return 3
            deployment = await lifecycle.get_deployment()
            active_version = (
                await session.get(ModelVersion, deployment.active_version_id)
                if deployment is not None
                else None
            )
            active_pr_auc = read_active_pr_auc(active_version)
            active_version_id = active_version.id if active_version is not None else None

            gates = ModelGates()
            split = split_dataset(*generate_dataset(rows, seed), seed)
            trained = train_candidate(split, gates, seed=seed)
            report = evaluate_gates(trained.metrics, trained.baseline_pr_auc, active_pr_auc, gates)
            slice_pr_aucs = tenant_slice_pr_aucs(
                split, trained, slices=settings.retrain_tenant_slices
            )
            slice_checks = evaluate_tenant_slices(slice_pr_aucs, active_pr_auc, gates)

            label = _version_label(seed, counts)
            label_window = f"matured<={now.date().isoformat()}"
            save_artifact(
                _artifacts_root(settings) / label,
                trained.booster,
                version_label=label,
                feature_spec=current_feature_spec(),
                calibration=trained.calibration,
                background=trained.background,
                metrics={
                    **trained.metrics.model_dump(),
                    "baseline_pr_auc": trained.baseline_pr_auc,
                },
            )
            _version_id, passed = await register_retrained_candidate(
                session,
                trained,
                report,
                slice_checks,
                slice_pr_aucs,
                version_label=label,
                artifact_uri=label,
                trigger=trigger,
                counts=counts,
                label_window=label_window,
                active_version_id=active_version_id,
                seed=seed,
                rows=rows,
            )
            await session.commit()
    finally:
        await engine.dispose()
    _print_report(label, report, slice_checks, passed)
    return 0 if passed else 2


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: retrain a candidate from matured reviewed labels."""
    parser = argparse.ArgumentParser(description="Retrain a candidate model from matured labels.")
    parser.add_argument(
        "--trigger",
        choices=[trigger.value for trigger in ModelTrigger],
        default=ModelTrigger.MANUAL.value,
        help="What initiated this run (manual | scheduled).",
    )
    parser.add_argument("--rows", type=int, default=_TRAIN_ROWS, help="Synthetic dataset size.")
    parser.add_argument("--seed", type=int, default=_SEED, help="Deterministic training seed.")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(ModelTrigger(args.trigger), args.rows, args.seed))


if __name__ == "__main__":
    raise SystemExit(main())
