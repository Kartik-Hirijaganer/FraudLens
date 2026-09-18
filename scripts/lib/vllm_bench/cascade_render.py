"""Summary: Deterministic Markdown rendering for the scenario-shaped gated-cascade report
(release 0.5.0 Phase 5, carried risk 8 and 11). Every figure and every sentence comes from the
validated typed report, so the rendered document cannot claim anything the measurements do not.

The cascade table puts p95 next to GPU-hours per case and aggregate resident memory on the SAME
row, and the comparison table labels each figure `same hardware` or `two endpoints`. Those two
choices are the whole point: a two-endpoint cascade p95 read beside a one-endpoint baseline p95
is an architecture comparison, and a reader who cannot see that will read a real cost as a free
win (AD-4.3, AD-4.4).

Key classes:
- (none)

Key functions:
- render_cascade_markdown: render provenance, scenarios, cascade behaviour, and disclosures.

Notes:
- Absent provenance renders as an explicit `not captured`, never as a blank or an inferred value:
  the 0.5.0 full run predates run-time CUDA capture and exercised no hosted tier.
- Accepted-draft quality deliberately omits abstention correctness. A scenario matrix accepts
  only served drafts, so an abstention rate computed over them would be a vacuous 1.0 —
  precisely the kind of self-agreeing metric this release exists to remove. Abstention is
  visible instead in the terminal-failure table, as cases refused before any model call.
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.vllm_bench.cascade_report_models import (
    CascadeBenchReport,
    CascadeComparison,
    ScenarioReport,
)

_ABSENT = "not captured"


def _provenance_rows(report: CascadeBenchReport) -> list[str]:
    """Render one row per endpoint role, including the optional CUDA runtime."""
    rows = []
    for endpoint in report.provenance.endpoints:
        server = endpoint.server
        rows.append(
            "| "
            + " | ".join(
                (
                    endpoint.role,
                    server.model,
                    server.model_revision[:12],
                    server.image_digest[:19],
                    server.gpu_name,
                    server.driver_version,
                    server.cuda_version or _ABSENT,
                    f"{server.weight_memory_gib:.4f}",
                    f"{server.kv_cache_tokens:,}",
                )
            )
            + " |"
        )
    return rows


def _scenario_rows(report: CascadeBenchReport) -> list[str]:
    """Render one row per scenario and measured level, request-level throughput and latency."""
    rows = []
    for scenario in report.scenarios:
        for level in scenario.levels:
            rows.append(
                "| "
                + " | ".join(
                    (
                        scenario.name,
                        "→".join(scenario.stages),
                        str(level.concurrency),
                        f"{level.latency_p50_ms:.1f}",
                        f"{level.latency_p95_ms:.1f}",
                        f"{level.requests_per_second:.3f}",
                        f"{level.useful_drafts_per_second:.3f}",
                        f"{level.error_rate:.4f}",
                        f"${level.cost_per_1000_drafts_usd:.4f}",
                    )
                )
                + " |"
            )
    return rows


def _cascade_rows(report: CascadeBenchReport) -> list[str]:
    """Render case-level cascade behaviour with its resource cost on the same row (AD-4.3)."""
    rows = []
    for scenario in report.cascade_scenarios:
        for level in scenario.levels:
            if level.cascade is None:
                continue
            memory = level.aggregate_memory_peak_mib
            rows.append(
                "| "
                + " | ".join(
                    (
                        scenario.name,
                        str(level.concurrency),
                        str(level.cascade.cases),
                        f"{level.cascade.escalation_rate:.1%}",
                        f"{level.cascade.final_pass_rate:.1%}",
                        f"{level.cascade.terminal_failure_rate:.1%}",
                        f"{level.cascade.latency_p50_ms:.1f}",
                        f"{level.cascade.latency_p95_ms:.1f}",
                        f"{level.cascade.gpu_seconds_per_case:.3f}",
                        f"{memory:.0f}" if memory is not None else _ABSENT,
                        str(len(scenario.endpoint_roles)),
                    )
                )
                + " |"
            )
    return rows


def _stage_rows(report: CascadeBenchReport) -> list[str]:
    """Render what each stage of each cascade actually cost, rejected tiers included."""
    rows = []
    for scenario in report.cascade_scenarios:
        for level in scenario.levels:
            if level.cascade is None:
                continue
            totals = level.cascade.stage_totals
            for stage in scenario.stages:
                errors = sum(totals.errors.get(stage, {}).values())
                rows.append(
                    "| "
                    + " | ".join(
                        (
                            scenario.name,
                            stage,
                            f"{level.cascade.stage_mix.get(stage, 0.0):.1%}",
                            f"{level.cascade.stage_pass_rate.get(stage, 0.0):.1%}",
                            f"{totals.latency_ms.get(stage, 0.0) / 1000:.1f}",
                            f"{totals.prompt_tokens.get(stage, 0):,}",
                            f"{totals.completion_tokens.get(stage, 0):,}",
                            str(totals.retries.get(stage, 0)),
                            str(errors),
                            f"${totals.cost_usd.get(stage, 0):.6f}",
                        )
                    )
                    + " |"
                )
    return rows


def _rejection_rows(report: CascadeBenchReport) -> list[str]:
    """Render WHY each scenario failed the cases it failed — the release's central evidence.

    Raw quantization and the gate that catches it are only legible together: a fabrication count
    next to a zero is the whole argument for escalating, and a terminal-failure rate with no
    reason codes behind it is a number nobody can act on.
    """
    rows = []
    for scenario in report.scenarios:
        for level in scenario.levels:
            if level.cascade is None:
                continue
            reasons = level.cascade.reason_counts
            rendered = (
                ", ".join(f"`{code}` x {count}" for code, count in sorted(reasons.items()))
                or "none recorded (terminal failures were refused before any model call)"
            )
            rows.append(
                "| "
                + " | ".join(
                    (
                        scenario.name,
                        str(level.concurrency),
                        f"{level.cascade.terminal_failure_rate:.1%}",
                        rendered,
                    )
                )
                + " |"
            )
    return rows


def _comparison_rows(comparisons: Sequence[CascadeComparison]) -> list[str]:
    """Render each comparison next to what it actually compares (AD-4.4, carried risk 8)."""
    return [
        "| "
        + " | ".join(
            (
                item.metric,
                str(item.concurrency),
                item.baseline_scenario,
                f"{item.baseline:.6g}",
                item.scenario,
                f"{item.observed:.6g}",
                f"{item.change_pct:+.1f}%",
                "same hardware" if item.same_resource else "two endpoints vs one",
            )
        )
        + " |"
        for item in comparisons
    ]


def _quality_rows(scenarios: Sequence[ScenarioReport]) -> list[str]:
    """Render accepted-draft quality per scenario: what the product actually served."""
    return [
        "| "
        + " | ".join(
            (
                item.name,
                str(item.quality.evaluated),
                f"{item.quality.schema_valid_rate:.4f}",
                f"{item.quality.reference_validity:.4f}",
                f"{item.quality.citation_recall:.4f}",
                f"{item.quality.required_fact_coverage:.4f}",
                str(item.quality.fabricated_reference_attempts),
                str(item.quality.unsupported_claim_flags),
            )
        )
        + " |"
        for item in scenarios
    ]


def _table(heading: str, columns: Sequence[str], rows: Sequence[str]) -> list[str]:
    """Render one Markdown section, or an explicit empty note when nothing was measured."""
    if not rows:
        return [heading, "", "_No measured rows._", ""]
    return [
        heading,
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *rows,
        "",
    ]


def render_cascade_markdown(report: CascadeBenchReport) -> str:
    """Render the complete gated-cascade evidence document from the validated typed report."""
    routes = report.provenance.external_routes
    lines = [
        "# Gated SAR cascade benchmark",
        "",
        f"**{report.headline}**",
        "",
        f"- Run: `{report.run_id}` ({report.protocol_version}, {report.report_version})",
        f"- Commit: `{report.provenance.git_commit}`",
        f"- Config SHA-256: `{report.config_sha256}`",
        f"- Cases SHA-256: `{report.cases_sha256}`",
        f"- Prompt: `{report.prompt_version}` (`{report.prompt_sha256}`)",
        f"- Quality policy SHA-256: `{report.provenance.quality_policy_sha256}`",
        f"- Measured cases: {report.measured_cases} from `{report.case_source}`",
        f"- Window: {report.started_at.isoformat()} → {report.completed_at.isoformat()}",
        f"- Baseline scenario: `{report.baseline_scenario}`",
        f"- Acceptance met: {'yes' if report.acceptance_met else 'NO'}",
        "",
        "## Endpoint provenance",
        "",
    ]
    lines += _table(
        "",
        (
            "Role",
            "Model",
            "Revision",
            "Image digest",
            "GPU",
            "Driver",
            "CUDA",
            "Weights GiB",
            "KV tokens",
        ),
        _provenance_rows(report),
    )[1:]
    lines += [
        "External hosted routes: "
        + (
            "none exercised by this run"
            if not routes
            else ", ".join(
                f"`{item.connection}` → `{item.model}` via {item.upstream_provider} "
                f"(ZDR {'asserted' if item.zero_data_retention else 'NOT asserted'}, "
                f"observed {item.captured_at.isoformat()})"
                for item in routes
            )
        )
        + ".",
        "",
    ]
    lines += _table(
        "## Scenario performance (model calls)",
        (
            "Scenario",
            "Stages",
            "Concurrency",
            "p50 ms",
            "p95 ms",
            "req/s",
            "useful drafts/s",
            "Error rate",
            "Cost / 1k",
        ),
        _scenario_rows(report),
    )
    lines += _table(
        "## Cascade behaviour (cases)",
        (
            "Scenario",
            "Concurrency",
            "Cases",
            "Escalated",
            "Served",
            "Terminal fail",
            "Case p50 ms",
            "Case p95 ms",
            "GPU s / case",
            "Aggregate peak MiB",
            "Endpoints",
        ),
        _cascade_rows(report),
    )
    lines += _table(
        "## Stage accounting",
        (
            "Scenario",
            "Stage",
            "Share served",
            "Pass rate reached",
            "Total s",
            "Prompt tokens",
            "Output tokens",
            "Retries",
            "Serving errors",
            "Provider cost",
        ),
        _stage_rows(report),
    )
    lines += _table(
        "## Terminal failures and why",
        ("Scenario", "Concurrency", "Terminal fail", "Reason codes"),
        _rejection_rows(report),
    )
    lines += _table(
        "## Comparisons",
        (
            "Metric",
            "Concurrency",
            "Baseline",
            "Baseline value",
            "Scenario",
            "Observed",
            "Change",
            "Compares",
        ),
        _comparison_rows(report.comparisons),
    )
    lines += _table(
        "## Accepted-draft quality",
        (
            "Scenario",
            "Accepted",
            "Schema valid",
            "Reference validity",
            "Citation recall",
            "Fact coverage",
            "Fabricated refs",
            "Unsupported claims",
        ),
        _quality_rows(report.scenarios),
    )
    lines += _table(
        "## Acceptance",
        ("Criterion", "Result", "Observed", "Required"),
        [
            f"| {item.name} | {'PASS' if item.passed else 'FAIL'} | {item.observed} "
            f"| {item.required} |"
            for item in report.acceptance
        ],
    )
    lines += ["## Disclosures", "", *[f"- {item}" for item in report.disclosures], ""]
    return "\n".join(lines)
