"""Summary: Scenario-shaped cascade report derivation, acceptance, rendering, and publication.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Every matrix here is produced by the real `run_scenario` against the scripted production-shaped
  drafter, so a level's evidence has the same shape a live run persists. No test asserts a figure
  it did not recompute from the fixture run.
- The parity test is the important one: it tampers with a recorded live gate verdict and requires
  publication to refuse. A report that could not tell those apart would be a second opinion about
  the shipped path rather than evidence about it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from cascade_fakes import ScriptedCascadeDrafter, cascade_artifact, cascade_config
from pydantic import ValidationError
from vllm_bench_fakes import FakeSampler, replay_gate, server, small_config

from fraudlens_backend.sar.factory import SarLlmConfig, load_sar_llm_config
from lib.study import canonical_json
from lib.vllm_bench.cascade_load import run_scenario
from lib.vllm_bench.cascade_render import render_cascade_markdown
from lib.vllm_bench.cascade_report import (
    build_cascade_report,
    load_cascade_report,
    write_cascade_report,
)
from lib.vllm_bench.cascade_report_models import (
    CASCADE_REPORT_VERSION,
    CascadeBenchReport,
    cascade_mechanical_headline,
)
from lib.vllm_bench.config import VllmBenchConfig, load_config
from lib.vllm_bench.publish import (
    CASCADE_REPORT_BASENAME,
    publish_cascade_report,
    validate_published_cascade_artifacts,
)
from lib.vllm_bench.state import CaseArtifact, RunManifest, case_artifact_sha256

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = "bf16-baseline"
_CASCADE = "awq-bf16"


def _sar_config(config: VllmBenchConfig) -> SarLlmConfig:
    """Load the production SAR routing whose profiles name the measured stages."""
    return load_sar_llm_config(_REPO_ROOT / "config" / config.cascade.sar_config_file)


def _matrix(
    tmp_path: Path, *, escalate: tuple[str, ...] = ("case-1",)
) -> tuple[VllmBenchConfig, CaseArtifact, RunManifest]:
    """Execute a baseline scenario and a cascade scenario into one scenario-shaped manifest."""
    config = cascade_config(small_config(load_config()))
    config = config.model_copy(
        update={
            "cascade": config.cascade.model_copy(
                update={"report": config.cascade.report.model_copy(update={"baseline": _BASELINE})}
            )
        }
    )
    artifact = cascade_artifact(config)
    run_path = tmp_path / "run.json"

    async def execute() -> RunManifest:
        manifest = None
        for name, stages, cases in (
            (_BASELINE, ("bf16",), ()),
            (_CASCADE, ("awq", "bf16"), escalate),
        ):
            scenario = config.cascade.scenario(name)
            manifest = await run_scenario(
                run_path=run_path,
                run_id="vllm-bench-0123456789abcdef",
                scenario=scenario,
                artifact=artifact,
                cases_sha256=case_artifact_sha256(artifact),
                config=config,
                profile="full",
                drafter_factory=lambda s=stages, c=cases: ScriptedCascadeDrafter(
                    stages=s if len(s) > 1 else (s[0], s[0]), escalate=c
                ),
                samplers={role: FakeSampler() for role in scenario.endpoints},
                provenance={
                    role: server(config, config.cascade.endpoints[role].arm)
                    for role in scenario.endpoints
                },
                git_commit="a" * 40,
            )
        assert manifest is not None
        return manifest

    return config, artifact, asyncio.run(execute())


def _report(tmp_path: Path, **kwargs) -> CascadeBenchReport:
    """Derive one complete cascade report from a freshly executed scenario matrix."""
    config, artifact, manifest = _matrix(tmp_path, **kwargs)
    return build_cascade_report(manifest, artifact, config, _sar_config(config), gate=replay_gate())


def test_the_report_derives_every_headline_figure_from_the_measured_matrix(
    tmp_path: Path,
) -> None:
    """A headline nobody can author: served share, escalated share, and p95 all recompute."""
    report = _report(tmp_path)

    cascade = report.scenario(_CASCADE)
    level = max(cascade.levels, key=lambda item: item.concurrency)
    assert level.cascade is not None
    assert report.report_version == CASCADE_REPORT_VERSION
    assert report.baseline_scenario == _BASELINE
    assert cascade.stages == ("awq", "bf16")
    # One of two measured cases escalates, and the second tier serves it.
    assert level.cascade.escalation_rate == pytest.approx(0.5)
    assert level.cascade.final_pass_rate == pytest.approx(1.0)
    assert f"{level.cascade.final_pass_rate:.1%} of {level.cascade.cases} cases" in report.headline
    assert f"{level.cascade.escalation_rate:.1%} escalated" in report.headline
    assert report.acceptance_met is True


def test_a_two_endpoint_comparison_is_never_published_as_a_same_hardware_result(
    tmp_path: Path,
) -> None:
    """A cascade p95 beside a one-endpoint baseline is an architecture comparison (AD-4.3)."""
    report = _report(tmp_path)

    cascade = {item.scenario: item for item in report.comparisons if item.scenario == _CASCADE}
    assert cascade
    assert all(not item.same_resource for item in report.comparisons if item.scenario == _CASCADE)
    assert "across two endpoints, not one" in report.headline
    rendered = render_cascade_markdown(report)
    assert "two endpoints vs one" in rendered


def test_publication_refuses_a_report_whose_verdicts_disagree_with_the_live_run(
    tmp_path: Path,
) -> None:
    """The recorded production verdict and the re-derived one must agree exactly, or no publish."""
    config, artifact, manifest = _matrix(tmp_path)
    key = f"{_CASCADE}:{max(config.load.concurrency_levels)}"
    level = manifest.levels[key]
    tampered = level.measurements[0].model_copy(
        update={"gate_passed": not level.measurements[0].gate_passed, "gate_reasons": ()}
    )
    manifest = manifest.model_copy(
        update={
            "levels": {
                **manifest.levels,
                key: level.model_copy(update={"measurements": (tampered, *level.measurements[1:])}),
            }
        }
    )

    report = build_cascade_report(
        manifest, artifact, config, _sar_config(config), gate=replay_gate()
    )

    parity = next(item for item in report.acceptance if item.name == "gate_verdict_parity")
    assert parity.passed is False
    assert report.acceptance_met is False
    assert report.headline.startswith("Acceptance NOT met (")
    assert "gate_verdict_parity" in report.headline
    with pytest.raises(ValueError, match="acceptance is unmet"):
        publish_cascade_report(report, config, tmp_path)


def test_a_reordered_level_cannot_report_because_it_measured_another_population(
    tmp_path: Path,
) -> None:
    """Case order is the fairness guarantee; a level that drifted is not comparable evidence."""
    config, artifact, manifest = _matrix(tmp_path)
    key = f"{_CASCADE}:{max(config.load.concurrency_levels)}"
    level = manifest.levels[key]
    manifest = manifest.model_copy(
        update={
            "levels": {
                **manifest.levels,
                key: level.model_copy(update={"measurements": tuple(reversed(level.measurements))}),
            }
        }
    )

    with pytest.raises(ValueError, match="request order drifted"):
        build_cascade_report(manifest, artifact, config, _sar_config(config), gate=replay_gate())


def test_a_run_from_a_superseded_protocol_still_reports_against_its_recorded_bytes(
    tmp_path: Path,
) -> None:
    """Editing the protocol file may never orphan already-measured evidence (AD-1.3)."""
    config, artifact, manifest = _matrix(tmp_path)
    superseded = "vllm-sar-bench-v1"
    lineage = {**config.protocol_lineage, superseded: config.config_sha256}
    moved = config.model_copy(update={"protocol_lineage": lineage, "config_sha256": "f" * 64})
    aged = manifest.model_copy(update={"protocol_version": superseded})

    report = build_cascade_report(aged, artifact, moved, _sar_config(moved), gate=replay_gate())

    assert report.protocol_version == superseded
    assert report.config_sha256 == config.config_sha256

    orphan = manifest.model_copy(update={"protocol_version": "vllm-sar-bench-unknown"})
    with pytest.raises(ValueError, match="no recorded config hash"):
        build_cascade_report(orphan, artifact, moved, _sar_config(moved), gate=replay_gate())


def test_a_matrix_with_no_multi_stage_scenario_is_not_a_cascade_report(tmp_path: Path) -> None:
    """A single-model matrix has nothing to escalate, so it may not publish as cascade evidence."""
    config, artifact, manifest = _matrix(tmp_path)
    key = f"{_CASCADE}:{max(config.load.concurrency_levels)}"
    manifest = manifest.model_copy(
        update={"levels": {k: v for k, v in manifest.levels.items() if k != key}}
    )

    with pytest.raises(ValueError, match="measured multi-stage scenario"):
        build_cascade_report(manifest, artifact, config, _sar_config(config), gate=replay_gate())


def test_publishing_installs_a_bound_pair_and_rejects_a_hand_edited_rendering(
    tmp_path: Path,
) -> None:
    """The committed Markdown must stay exactly what the typed report renders."""
    report = _report(tmp_path)
    config = cascade_config(small_config(load_config()))
    config = config.model_copy(
        update={
            "cascade": config.cascade.model_copy(
                update={"report": config.cascade.report.model_copy(update={"baseline": _BASELINE})}
            )
        }
    )

    result = publish_cascade_report(report, config, tmp_path)

    assert result.report_json_path.name == f"{CASCADE_REPORT_BASENAME}.json"
    assert validate_published_cascade_artifacts(result.report_json_path, config) == report
    assert report.disclosures == config.cascade.report.disclosures
    markdown = result.report_markdown_path
    markdown.write_text(markdown.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Markdown rendering drifted"):
        validate_published_cascade_artifacts(result.report_json_path, config)


def test_a_local_report_round_trips_through_its_canonical_bytes(tmp_path: Path) -> None:
    """A written report must parse back identically so a resumed session reads one truth."""
    report = _report(tmp_path)

    write_cascade_report(tmp_path, report)

    assert load_cascade_report(tmp_path / "cascade-report.json") == report
    assert (tmp_path / "cascade-report.md").read_text(encoding="utf-8") == (
        render_cascade_markdown(report)
    )
    assert json.loads(canonical_json(report))["reportVersion"] == CASCADE_REPORT_VERSION


def test_the_published_full_run_reports_the_measurements_the_ledger_recorded() -> None:
    """The committed cascade artifact must still say what the ledger says the run measured."""
    published = load_cascade_report(
        _REPO_ROOT / "docs/reference/benchmarks/vllm-gated-cascade-benchmark.json"
    )

    cascade = published.scenario("awq-bf16-unconstrained")
    level = max(cascade.levels, key=lambda item: item.concurrency)
    assert level.cascade is not None
    assert published.run_id == "vllm-bench-be12675628805a53"
    assert level.cascade.cases == 1000
    assert level.cascade.final_pass_rate == pytest.approx(0.997)
    assert level.cascade.escalation_rate == pytest.approx(0.092)
    assert cascade.quality.reference_validity == pytest.approx(1.0)
    assert published.weight_memory_reduction == pytest.approx(0.6348, abs=1e-4)
    assert published.acceptance_met is True
    assert all(item.server.cuda_version is None for item in published.provenance.endpoints)
    assert published.provenance.external_routes == ()


def _mutate(report: CascadeBenchReport, **changes) -> None:
    """Rebuild one report with changed fields so its validators run over the new shape."""
    CascadeBenchReport(**{**report.model_dump(), **changes})


def test_report_invariants_reject_every_shape_the_evidence_cannot_support(
    tmp_path: Path,
) -> None:
    """A report is a claim about a run; each invariant is a way that claim could be false."""
    report = _report(tmp_path)
    cascade = report.scenario(_CASCADE)

    with pytest.raises(ValueError, match="report has no scenario"):
        report.scenario("never-measured")
    with pytest.raises(ValidationError, match="scenario names must be unique"):
        _mutate(report, scenarios=(cascade, cascade))
    with pytest.raises(ValidationError, match="baselineScenario must name"):
        _mutate(report, baseline_scenario="never-measured")
    with pytest.raises(ValidationError, match="roles without provenance"):
        _mutate(
            report,
            provenance=report.provenance.model_copy(
                update={"endpoints": report.provenance.endpoints[:1]}
            ),
        )
    with pytest.raises(ValidationError, match="acceptanceMet must equal"):
        _mutate(report, acceptance_met=not report.acceptance_met)
    with pytest.raises(ValidationError, match="mechanically derived"):
        _mutate(report, headline="AWQ is simply faster.")
    with pytest.raises(ValidationError, match="unique and in stable role order"):
        report.provenance.model_copy(
            update={"endpoints": tuple(reversed(report.provenance.endpoints))}
        ).model_validate(
            report.provenance.model_copy(
                update={"endpoints": tuple(reversed(report.provenance.endpoints))}
            ).model_dump()
        )
    with pytest.raises(ValidationError, match="unique and ascending"):
        cascade.model_validate(
            {**cascade.model_dump(), "levels": tuple(reversed(report.scenario(_BASELINE).levels))}
        )


def test_a_single_model_report_cannot_borrow_a_cascade_headline(tmp_path: Path) -> None:
    """With no multi-stage scenario there is no escalation to describe, so there is no headline."""
    report = _report(tmp_path)

    with pytest.raises(ValueError, match="requires at least one multi-stage scenario"):
        cascade_mechanical_headline(
            scenarios=(report.scenario(_BASELINE),),
            comparisons=report.comparisons,
            weight_memory_reduction=report.weight_memory_reduction,
            acceptance=report.acceptance,
        )
    with pytest.raises(ValidationError, match="at least one multi-stage scenario"):
        _mutate(report, scenarios=(report.scenario(_BASELINE),))


def test_an_incomplete_or_mismatched_matrix_never_reports(tmp_path: Path) -> None:
    """A run still executing, or bound to another corpus, is not publishable evidence."""
    config, artifact, manifest = _matrix(tmp_path)
    sar = _sar_config(config)

    with pytest.raises(ValueError, match="must be complete before reporting"):
        build_cascade_report(
            manifest.model_copy(update={"completed_at": None}),
            artifact,
            config,
            sar,
            gate=replay_gate(),
        )
    with pytest.raises(ValueError, match="configuration hash drifted"):
        build_cascade_report(
            manifest.model_copy(update={"config_sha256": "0" * 64}),
            artifact,
            config.model_copy(update={"config_sha256": "0" * 64}),
            sar,
            gate=replay_gate(),
        )
    with pytest.raises(ValueError, match="does not match the canonical case artifact"):
        build_cascade_report(
            manifest.model_copy(update={"cases_sha256": "1" * 64}),
            artifact,
            config,
            sar,
            gate=replay_gate(),
        )
    empty = manifest.model_copy(update={"levels": {}})
    with pytest.raises(ValueError, match="measured no configured cascade scenario"):
        build_cascade_report(empty, artifact, config, sar, gate=replay_gate())


def test_an_undeclared_production_profile_cannot_be_reported(tmp_path: Path) -> None:
    """Stage names come from production routing; a profile it omits cannot be reported."""
    config, artifact, manifest = _matrix(tmp_path)
    stripped = _sar_config(config)
    stripped = stripped.model_copy(
        update={
            "profiles": {
                name: tiers
                for name, tiers in stripped.profiles.items()
                if name != config.cascade.scenario(_CASCADE).profile
            }
        }
    )

    with pytest.raises(ValueError, match="declares no profile"):
        build_cascade_report(manifest, artifact, config, stripped, gate=replay_gate())
