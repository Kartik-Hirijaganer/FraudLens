"""Summary: Deterministic README benchmark and Make-target region renderers.

Key classes:
- (none)

Key functions:
- render_vllm_benchmark: render measured inference evidence or an explicit pending state.
- render_cascade_benchmark: render the measured gated-cascade comparison, one row per scenario.
- render_fulldata_training: render measured training evidence or an explicit pending state.
- render_k8s_benchmark: render every published Kubernetes HPA and durability run, one row each.
- render_make_targets: derive the curated developer-command table from Makefile help text.

Notes:
- Missing paid-experiment artifacts never become zeroes or synthetic placeholder measurements.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from lib.fulldata.report import FrontendFullDataProjection
from lib.k8s_demo.evidence import HpaEvidenceReport, validate_evidence
from lib.vllm_bench.cascade_report_models import CascadeBenchReport
from lib.vllm_bench.report_models import FrontendVllmBenchData

_VLLM_PATH = Path("frontend/src/data/vllm-awq-sar-benchmark.json")
_FULLDATA_PATH = Path("frontend/src/data/ibm-full-data-training.json")
_CASCADE_PATH = Path("docs/reference/benchmarks/vllm-gated-cascade-benchmark.json")
_K8S_PATHS = (
    Path("docs/reference/benchmarks/k8s-hpa-scaling.json"),
    Path("docs/reference/benchmarks/aks-hpa-scaling.json"),
)
_MAKE_TARGETS = (
    "install",
    "run",
    "local-demo",
    "test",
    "coverage",
    "pre-pr",
    "pr-check",
    "docs",
    "fulldata-pilot",
    "fulldata-validate",
    "vllm-bench-validate",
    "runpod-gpu-plan",
    "kind-demo",
    "k8s-validate",
    "tf-validate",
)
_HELP_PATTERN = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_-]*):.*?## (.+)$", re.MULTILINE)


def _optional_model(path: Path, model_type: type[BaseModel]) -> BaseModel | None:
    """Load a strict published projection, returning None only when it does not exist."""
    if not path.is_file():
        return None
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def render_vllm_benchmark(repo_root: Path) -> str:
    """Render the published inference headline without inventing absent measurements."""
    parsed = _optional_model(repo_root / _VLLM_PATH, FrontendVllmBenchData)
    if parsed is None:
        return "\n".join(
            [
                "| Status | Evidence |",
                "| --- | --- |",
                "| Pending | No Phase 11 GPU benchmark has been run or published. |",
            ]
        )
    report = FrontendVllmBenchData.model_validate(parsed)
    bf16, awq = report.arms
    return "\n".join(
        [
            "| Cases | BF16 weight memory | AWQ weight memory | Reduction | Acceptance |",
            "| ---: | ---: | ---: | ---: | --- |",
            f"| {report.measured_cases} | {bf16.server.weight_memory_gib:.2f} GiB | "
            f"{awq.server.weight_memory_gib:.2f} GiB | {report.weight_memory_reduction:.1%} | "
            f"{'met' if report.acceptance_met else 'not met'} |",
            "",
            report.headline,
        ]
    )


def render_cascade_benchmark(repo_root: Path) -> str:
    """Render each measured scenario at its highest concurrency, from the published report.

    Every figure is read off the typed report rather than typed into the README, because a
    hand-copied benchmark number is exactly the drift this release exists to remove.
    """
    parsed = _optional_model(repo_root / _CASCADE_PATH, CascadeBenchReport)
    if parsed is None:
        return "\n".join(
            [
                "| Status | Evidence |",
                "| --- | --- |",
                "| Pending | No gated-cascade matrix has been published. |",
            ]
        )
    report = CascadeBenchReport.model_validate(parsed)
    lines = [
        "| Scenario | Cases served | Citation fabrications | Escalated | Case p95 | "
        "GPU-hours / case | Endpoints |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in report.scenarios:
        level = max(scenario.levels, key=lambda item: item.concurrency)
        if level.cascade is None:
            continue
        fabricated = level.cascade.reason_counts.get("citation_fabricated", 0)
        gpu_hours = level.gpu_hours_per_case
        lines.append(
            f"| {scenario.name} | {level.cascade.final_pass_rate:.1%} | {fabricated} | "
            f"{level.cascade.escalation_rate:.1%} | {level.cascade.latency_p95_ms:,.0f} ms | "
            f"{gpu_hours:.6f} | {len(scenario.endpoint_roles)} |"
            if gpu_hours is not None
            else f"| {scenario.name} | {level.cascade.final_pass_rate:.1%} | {fabricated} | "
            f"{level.cascade.escalation_rate:.1%} | {level.cascade.latency_p95_ms:,.0f} ms | "
            f"n/a | {len(scenario.endpoint_roles)} |"
        )
    lines += ["", report.headline]
    return "\n".join(lines)


def render_fulldata_training(repo_root: Path) -> str:
    """Render published IBM row reconciliation and metrics, or an explicit pending state."""
    parsed = _optional_model(repo_root / _FULLDATA_PATH, FrontendFullDataProjection)
    if parsed is None:
        return "\n".join(
            [
                "| Status | Evidence |",
                "| --- | --- |",
                "| Pending | Phase 6 pilots exist locally; the final Medium aggregate is not "
                "published. |",
            ]
        )
    report = FrontendFullDataProjection.model_validate(parsed)
    lines = [
        "| Candidate | Source rows | Usable rows | Training rows | Holdout rows | PR-AUC | Gates |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {item.candidate} | {item.source_rows} | {item.usable_rows} | {item.training_rows} | "
        f"{item.evaluation_rows} | {item.pr_auc:.4f} | "
        f"{'passed' if item.gates_passed else 'failed'} |"
        for item in report.candidates
    )
    return "\n".join(lines)


def _k8s_evidence_row(repo_root: Path, path: Path) -> str | None:
    """Render one validated HPA run, or None when that platform has published nothing."""
    parsed = _optional_model(repo_root / path, HpaEvidenceReport)
    if parsed is None:
        return None
    report = HpaEvidenceReport.model_validate(parsed)
    validate_evidence(report)
    summary = report.summary
    durability = report.durability
    return (
        f"| {report.platform} | {summary.replicas_min_observed} → "
        f"{summary.replicas_max_observed} → {summary.replicas_min_observed} | "
        f"{summary.seconds_to_first_scale_up} s | {summary.seconds_to_scale_back_to_min} s | "
        f"{durability.runs_completed}/{durability.runs_submitted} | {durability.runs_failed} |"
    )


def render_k8s_benchmark(repo_root: Path) -> str:
    """Render every published Kubernetes run; an unrun platform contributes no row."""
    rows = [row for path in _K8S_PATHS if (row := _k8s_evidence_row(repo_root, path))]
    if not rows:
        return "\n".join(
            [
                "| Status | Evidence |",
                "| --- | --- |",
                "| Pending | No Kubernetes HPA evidence has been published. |",
            ]
        )
    return "\n".join(
        [
            "| Platform | API replicas | First scale-up | Scale-back | Durable runs | "
            "Failed runs |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
            *rows,
        ]
    )


def render_make_targets(repo_root: Path) -> str:
    """Render curated developer commands using descriptions owned by the root Makefile."""
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    descriptions = dict(_HELP_PATTERN.findall(makefile))
    missing = [target for target in _MAKE_TARGETS if target not in descriptions]
    if missing:
        raise ValueError(f"README Make targets missing help text: {', '.join(missing)}")
    lines = ["| Command | What it does |", "| --- | --- |"]
    lines.extend(f"| `make {target}` | {descriptions[target]} |" for target in _MAKE_TARGETS)
    return "\n".join(lines)
