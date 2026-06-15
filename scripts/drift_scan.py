"""Summary: The advisory model-drift scan Job (plan §16 Phase 10, §9.2, §10.5). It is the
scheduled / manually-triggered Container Apps Job that watches the ACTIVE model for score drift
and records an ADVISORY `drift_reports` row — it only signals (never gates serving or rolls back;
`advisory=True` always, the §10.5 "drift advisory only" rule). Because `model_inference_logs` are
hash-only (probability + `feature_hash`, never feature values — ADR-015), drift here is SCORE
drift: it computes the Population Stability Index (PSI) of the active model's recent inference
probabilities against an earlier reference window of the same model, classifies the severity from
standard PSI bands, and persists the report (+ a `job_executions(drift_scan)` row). With too few
inferences to be meaningful it records a low-severity "insufficient data" advisory rather than a
false alarm. Inferences are aggregated across agencies (the model is global) and carry no PHI.

Key classes:
- (none)

Key functions:
- population_stability_index: PSI between a reference and an actual probability sample (pure).
- severity_for_psi: map a PSI value onto the advisory severity band (pure).
- scan_active_model: compute + persist the advisory drift report for the active model version.
- main: CLI entry point — scan the active model (dev/demo only).

Notes:
- PSI is computed over equal-width [0,1] probability bins with a small epsilon floor so an empty
  bin never yields a log(0)/divide-by-zero; identical distributions give PSI ≈ 0.
- The report is ALWAYS advisory: a high PSI surfaces in the model-admin UI for a human to act on
  (e.g. trigger a retrain) — it never changes the active/canary pointer on its own.
"""

from __future__ import annotations

import argparse
import asyncio

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    DriftReport,
    JobExecution,
    JobStatus,
    JobType,
    ModelVersion,
    Severity,
)
from fraudlens_backend.db.repositories import ModelLifecycleRepository
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.settings import get_settings

# PSI interpretation bands (industry-standard): < 0.1 stable, < 0.25 moderate, < 0.5 significant.
_PSI_LOW = 0.1
_PSI_MEDIUM = 0.25
_PSI_HIGH = 0.5
# Minimum inferences (per window) for a PSI to be meaningful; below it the report is advisory-low.
_MIN_DRIFT_SAMPLES = 20
_PSI_BINS = 10
_EPS = 1e-6


def population_stability_index(
    reference: np.ndarray, actual: np.ndarray, *, bins: int = _PSI_BINS
) -> float:
    """Return the PSI of `actual` vs `reference` over equal-width [0,1] probability bins (pure)."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    edges[-1] = np.nextafter(1.0, 2.0)  # include 1.0 in the last bin
    ref_counts = np.histogram(reference, bins=edges)[0].astype(float)
    act_counts = np.histogram(actual, bins=edges)[0].astype(float)
    ref_pct = np.clip(ref_counts / max(reference.size, 1), _EPS, None)
    act_pct = np.clip(act_counts / max(actual.size, 1), _EPS, None)
    return float(np.sum((act_pct - ref_pct) * np.log(act_pct / ref_pct)))


def severity_for_psi(psi: float) -> Severity:
    """Map a PSI value onto the advisory severity band (plan §9.2 `drift_reports.severity`)."""
    if psi < _PSI_LOW:
        return Severity.LOW
    if psi < _PSI_MEDIUM:
        return Severity.MEDIUM
    if psi < _PSI_HIGH:
        return Severity.HIGH
    return Severity.CRITICAL


async def scan_active_model(
    session: AsyncSession, *, min_samples: int = _MIN_DRIFT_SAMPLES, bins: int = _PSI_BINS
) -> DriftReport | None:
    """Compute + persist the advisory score-drift report for the active model (None when no model).

    Splits the active model's inference probabilities in half by ingestion order — the earlier half
    is the reference, the later half the current window — and reports their PSI. Records a
    low-severity "insufficient data" advisory when there are too few inferences to be meaningful.
    """
    lifecycle = ModelLifecycleRepository(session)
    deployment = await lifecycle.get_deployment()
    if deployment is None:
        return None
    active = await session.get(ModelVersion, deployment.active_version_id)
    if active is None:
        return None
    probabilities = await lifecycle.inference_probabilities(active.id)
    samples = len(probabilities)
    if samples < 2 * min_samples:
        report = DriftReport(
            model_version_id=active.id,
            window=f"samples={samples}",
            metrics={"psi": 0.0, "samples": samples, "note": "insufficient_data"},
            severity=Severity.LOW,
            advisory=True,
        )
    else:
        midpoint = samples // 2
        reference = np.asarray(probabilities[:midpoint])
        current = np.asarray(probabilities[midpoint:])
        psi = population_stability_index(reference, current, bins=bins)
        report = DriftReport(
            model_version_id=active.id,
            window=f"samples={samples}",
            metrics={
                "psi": psi,
                "samples": samples,
                "referenceMean": float(reference.mean()),
                "currentMean": float(current.mean()),
            },
            severity=severity_for_psi(psi),
            advisory=True,
        )
    session.add(report)
    await session.flush()
    session.add(
        JobExecution(
            agency_id=None,
            job_type=JobType.DRIFT_SCAN,
            status=JobStatus.SUCCEEDED,
            payload={"model_version_label": active.version_label},
            result={"severity": report.severity.value, "samples": samples},
            attempts=1,
        )
    )
    await session.flush()
    return report


async def _amain() -> int:
    """Build the engine + scan the active model for advisory drift (dev/demo only)."""
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("drift-scan failed: DATABASE_URL is not configured")
        return 1
    try:
        async with build_sessionmaker(engine)() as session:
            report = await scan_active_model(session)
            await session.commit()
    finally:
        await engine.dispose()
    if report is None:
        print("drift-scan skipped: no active model deployment")
        return 0
    print(
        f"drift-scan OK (advisory): severity={report.severity.value} "
        f"psi={report.metrics.get('psi')} samples={report.metrics.get('samples')}"
    )
    return 0


def main() -> int:
    """CLI entry point: run the advisory drift scan and return its exit code."""
    argparse.ArgumentParser(description="Advisory model score-drift scan.").parse_args()
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
