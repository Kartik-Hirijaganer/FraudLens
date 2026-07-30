"""Summary: Thin CLI over the portfolio-demo bootstrap (plan §16 Phase 6). Every decision — the
guards, the operational-state detection, the model-state matrix, the ingest, the scoring, the
configured alert/SAR transitions, the verification, and the story's `job_executions` row — lives in
`fraudlens_backend.portfolio_demo.bootstrap` so it is importable and testable; this file only parses
arguments, builds the engine and the heavy pipeline components, injects `activate_model.py`'s
register+promote chain (which lives in `scripts/` and is therefore not importable from the backend),
prints a PHI-free outcome, and maps a refusal onto a non-zero exit code. `apply` is the default;
`--probe` reports calibration without persisting a run; `--verify` prints the read-only
expected-vs-actual table; `--reset` rebuilds the pinned baseline from scratch; `--config` points at
an explicit story document.

Key classes:
- (none)

Key functions:
- promote_configured_model: register + promote the configured bundle via the activate_model chain.
- main: parse arguments, run the requested mode, and return its exit code.

Notes:
- Refuses `environment == "prod"` unless `portfolio_demo_enabled` is set, and every guard failure
  prints one PHI-free reason and exits 1 — never a stack trace, never an authored payload.
- `--probe` writes the authored transactions (the rules engine windows same-account history out
  of `transactions`) but no run, band, alert, or SAR draft; it never writes `expected` to the YAML.
- `--verify` writes nothing at all: it re-reads live state, prints the operator-facing table, and
  exits non-zero on any delta. It runs no preflight guard on purpose — a missing model bundle or a
  drifted tenant must be REPORTED as a row, not turned into a refusal that hides the table.
- `--probe`, `--verify`, and `--reset` are mutually exclusive: two of them are read-only modes and
  the third is the destructive apply variant, so combining them can only be a mistake.
- The pinned bundle is not tracked in git, so a fresh clone gets an explicit "train or fetch it"
  refusal from the bundle guard rather than a confusing scoring failure later.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from activate_model import discover_bundles, promote_to_active, register_bundle
from fraudlens_backend.db.session import build_sessionmaker, create_engine_from_settings
from fraudlens_backend.pipeline_wiring import build_pipeline_components
from fraudlens_backend.portfolio_demo import (
    PortfolioDemoConfig,
    PortfolioDemoConfigError,
    load_portfolio_demo_config,
)
from fraudlens_backend.portfolio_demo.bootstrap import BootstrapRefusedError, apply_story
from fraudlens_backend.portfolio_demo.ingest import StoryIngestError
from fraudlens_backend.portfolio_demo.probe import probe_story, render_probe_report
from fraudlens_backend.portfolio_demo.verification import format_deltas, verify_story
from fraudlens_backend.settings import AppSettings, get_settings
from train_model import _artifacts_root

_FAILURE = 1
_SUCCESS = 0


async def promote_configured_model(session: AsyncSession, *, version_label: str) -> str:
    """Register + promote the configured bundle through `activate_model.py`'s real chain."""
    bundles = discover_bundles(_artifacts_root(get_settings()), label=version_label)
    if not bundles:
        raise BootstrapRefusedError(
            f"no gates-passed bundle '{version_label}' is available to promote — train or fetch it"
        )
    bundle = bundles[0]
    version = await register_bundle(session, bundle)
    return await promote_to_active(session, version, bundle)


def _models_dir(settings: AppSettings) -> Path:
    """Resolve the artifacts root the scorer loads from (relative values anchor at the repo)."""
    return _artifacts_root(settings)


async def _run_probe(config: PortfolioDemoConfig, settings: AppSettings) -> int:
    """Report calibration for every candidate without persisting a run."""
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("portfolio-demo probe failed: DATABASE_URL is not configured")
        return _FAILURE
    try:
        async with build_sessionmaker(engine)() as session:
            report = await probe_story(session, config, settings, models_dir=_models_dir(settings))
    finally:
        await engine.dispose()
    print(render_probe_report(report))
    return _SUCCESS


async def _run_verify(config: PortfolioDemoConfig, settings: AppSettings) -> int:
    """Print the read-only expected-vs-actual table; exit non-zero when any row differs."""
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("portfolio-demo verify failed: DATABASE_URL is not configured")
        return _FAILURE
    try:
        async with build_sessionmaker(engine)() as session:
            report = await verify_story(session, config)
    finally:
        await engine.dispose()
    print(report.render())
    if report.ok:
        print(f"portfolio-demo verify OK: story {report.story_version} matches its configuration")
        return _SUCCESS
    print(
        f"portfolio-demo verify FAILED: {len(report.deltas)} check(s) differ — "
        f"{format_deltas(report.deltas)}"
    )
    return _FAILURE


async def _run_apply(config: PortfolioDemoConfig, settings: AppSettings, *, reset: bool) -> int:
    """Bootstrap (or reset + rebuild) the configured story and verify it against `expected`."""
    engine = create_engine_from_settings(settings)
    if engine is None:
        print("portfolio-demo bootstrap failed: DATABASE_URL is not configured")
        return _FAILURE
    components = build_pipeline_components(settings)
    try:
        async with build_sessionmaker(engine)() as session:
            summary, report = await apply_story(
                session,
                config,
                settings,
                components=components,
                models_dir=_models_dir(settings),
                promote=promote_configured_model,
                reset=reset,
            )
    finally:
        await engine.dispose()
    print(report.render())
    print(
        f"portfolio-demo bootstrap OK: story {summary.story_version} on "
        f"{summary.model_version_label} ({summary.model_outcome}); "
        f"{summary.transactions_created} row(s) created, {summary.transactions_existing} existing, "
        f"{summary.scored} scored ({summary.already_scored} already scored), "
        f"{summary.alert_transitions} alert + {summary.sar_transitions} SAR transition(s)"
    )
    return _SUCCESS


async def _amain(args: argparse.Namespace) -> int:
    """Load the story, dispatch the requested mode, and map any refusal onto an exit code."""
    settings = get_settings()
    try:
        path = Path(args.config) if args.config else None
        config = load_portfolio_demo_config(path, settings=settings)
    except PortfolioDemoConfigError as exc:
        print(f"portfolio-demo bootstrap refused: {exc}")
        return _FAILURE
    try:
        if args.probe:
            return await _run_probe(config, settings)
        if args.verify:
            return await _run_verify(config, settings)
        return await _run_apply(config, settings, reset=args.reset)
    except (BootstrapRefusedError, StoryIngestError) as exc:
        print(f"portfolio-demo bootstrap refused: {exc}")
        return _FAILURE


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: apply, probe, or reset the configured portfolio demo story."""
    parser = argparse.ArgumentParser(
        description="Apply, probe, verify, or reset the configured portfolio demo story."
    )
    # Two read-only reports and one destructive apply variant: combining them is always a mistake.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--probe",
        action="store_true",
        help="Report calibration (policy header, per-row p/r/codes/band) without persisting runs.",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="Print the read-only expected-vs-actual table; exit non-zero on any mismatch.",
    )
    mode.add_argument(
        "--reset",
        action="store_true",
        help="Delete the tenant's operational rows first, then rebuild the pinned baseline.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to an explicit story document (defaults to the layered config location).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
