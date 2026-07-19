"""Summary: Run-directory IO + validated atomic publication for the GFP study (GFP
plan Phases 5-6). A benchmark run writes `.local/gfp-study/<run-id>/study.json` (the
typed StudyReport) and `motifs.json` (the curated, already-redacted motifs); `publish`
validates completeness — engine MUST be snapml, every configured dataset fully
evaluated, exactly one motif per typology (a missing cross-tenant cycle fails
publication) — plus a redaction scan, then ATOMICALLY writes the three committed
artifacts: the study report JSON + Markdown under `docs/reference/benchmarks/` and the
frontend visual JSON embedding the report's SHA-256. The Markdown is rendered SOLELY
from the typed report; the neutral-wording rule (say "cost of isolation" only for a
positive delta, positive resume wording only when the interval supports it) is applied
at render time from the SIGNED values.

Key classes:
- CuratedRunPayload: the run-dir curation payload (agency names, missing, motifs).
- PublishResult: the three committed paths + the embedded report hash.

Key functions:
- write_run_artifacts: write study.json + motifs.json into one run directory.
- load_run: parse one run directory back into the typed report + curation payload.
- validate_publishable: every reason a run may not be committed (raises ValueError).
- render_markdown: the committed Markdown, rendered solely from the typed models.
- publish_run: validate, then atomically write the three committed artifacts.
- validate_published_artifacts: assert the two committed JSONs cannot have drifted.

Notes:
- Writes are tmp-file + os.replace in the target directory, so a crashed publish never
  leaves a half-written committed artifact.
- The frontend payload embeds SHA-256(report JSON bytes); `validate_published_artifacts`
  recomputes it, which is the "two committed artifacts can't drift" contract.
- The redaction scan rejects the account-key separator, home-directory prefixes, and
  local scratch paths in any committed byte stream (Phase-8 tests broaden this).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from lib.gfp.boundaries import CuratedMotif, CuratedVisualData, StudyHighlightMetrics
from lib.gfp.config import GfpBenchmarkConfig
from lib.gfp.curation import TYPOLOGIES
from lib.gfp.report import ArmDelta, ArmMetrics, ScopeComparison, StudyReport

STUDY_JSON = "study.json"
MOTIFS_JSON = "motifs.json"
REPORT_BASENAME = "gfp-tenant-isolation-study"

_PUBLISHABLE_ENGINE = "snapml"
_JSON_INDENT = 2
# Every (arm, scope) evaluation one dataset must carry, and the delta pairs per scope.
_EXPECTED_ARM_SCOPES: tuple[tuple[str, str], ...] = (
    ("A", "shared"),
    ("B", "global"),
    ("C", "global"),
    ("B", "per_tenant"),
    ("C", "per_tenant"),
)
_EXPECTED_DELTA_PAIRS: tuple[tuple[str, str], ...] = (("A", "B"), ("B", "C"), ("A", "C"))
_GRAPH_SCOPES: tuple[str, ...] = ("global", "per_tenant")
# Byte patterns that must never reach a committed artifact (redaction fail-closed).
_REDACTION_FORBIDDEN: tuple[str, ...] = ("\x1f", "/Users/", "/home/", "C:\\", ".local/")
_RESUME_DATASET_CONTEXT = "full"


class CuratedRunPayload(BaseModel):
    """The run-directory curation payload the publish step binds to a report hash."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
    )

    agency_names: tuple[str, ...] = Field(
        ..., min_length=1, description="Demo-agency display names, indexed by agency index."
    )
    missing_typologies: tuple[str, ...] = Field(
        default=(),
        description="Typologies curation found NO real candidate for (publication blockers).",
    )
    motifs: tuple[CuratedMotif, ...] = Field(
        default=(), description="The curated, already-redacted motifs."
    )


@dataclass(frozen=True)
class PublishResult:
    """The three committed artifact paths + the report hash the frontend payload embeds."""

    report_json_path: Path
    report_markdown_path: Path
    frontend_json_path: Path
    report_sha256: str


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically (tmp file + os.replace) so no partial artifact survives."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _report_json(report: StudyReport) -> str:
    """Serialize the report exactly as committed (camelCase, stable key order)."""
    return json.dumps(
        report.model_dump(mode="json", by_alias=True), indent=_JSON_INDENT, sort_keys=True
    )


def write_run_artifacts(run_dir: Path, report: StudyReport, payload: CuratedRunPayload) -> None:
    """Write study.json + motifs.json into one local (never committed) run directory."""
    _atomic_write_text(run_dir / STUDY_JSON, _report_json(report) + "\n")
    _atomic_write_text(
        run_dir / MOTIFS_JSON,
        json.dumps(
            payload.model_dump(mode="json", by_alias=True), indent=_JSON_INDENT, sort_keys=True
        )
        + "\n",
    )


def load_run(run_dir: Path) -> tuple[StudyReport, CuratedRunPayload]:
    """Parse one run directory back into the typed report + curation payload."""
    study_path = run_dir / STUDY_JSON
    motifs_path = run_dir / MOTIFS_JSON
    if not study_path.is_file() or not motifs_path.is_file():
        raise FileNotFoundError(
            f"run directory {run_dir.name} is missing {STUDY_JSON} or {MOTIFS_JSON} — "
            "run the benchmark to completion before publishing"
        )
    report = StudyReport.model_validate_json(study_path.read_text(encoding="utf-8"))
    payload = CuratedRunPayload.model_validate_json(motifs_path.read_text(encoding="utf-8"))
    return report, payload


def _dataset_metrics(report: StudyReport, source: str) -> list[ArmMetrics]:
    """All of one dataset's arm evaluations."""
    return [metric for metric in report.metrics if metric.dataset_source == source]


def _validate_dataset_completeness(report: StudyReport, source: str) -> list[str]:
    """Reasons one configured dataset's evaluations are incomplete."""
    reasons: list[str] = []
    present = {(metric.arm, metric.scope) for metric in _dataset_metrics(report, source)}
    for arm, scope in _EXPECTED_ARM_SCOPES:
        if (arm, scope) not in present:
            reasons.append(f"dataset '{source}' is missing the Arm {arm} ({scope}) evaluation")
    delta_keys = {
        (delta.from_arm, delta.to_arm, delta.scope)
        for delta in report.deltas
        if delta.dataset_source == source
    }
    for scope in _GRAPH_SCOPES:
        for from_arm, to_arm in _EXPECTED_DELTA_PAIRS:
            if (from_arm, to_arm, scope) not in delta_keys:
                reasons.append(
                    f"dataset '{source}' is missing the {from_arm}->{to_arm} ({scope}) delta"
                )
    if not any(comparison.dataset_source == source for comparison in report.comparisons):
        reasons.append(f"dataset '{source}' is missing its isolation comparison")
    if not any(item.spec.source == source for item in report.datasets):
        reasons.append(f"dataset '{source}' is missing its provenance record")
    return reasons


def validate_publishable(
    report: StudyReport, payload: CuratedRunPayload, config: GfpBenchmarkConfig
) -> None:
    """Raise ValueError with every reason this run may not be committed.

    Publication requires the pinned snapml engine, a complete evaluation grid for
    every configured dataset, no missing curated typology (the cross-tenant cycle is
    the visual's point), and exactly one motif per typology.
    """
    reasons: list[str] = []
    if report.engine_name != _PUBLISHABLE_ENGINE:
        reasons.append(
            f"engine '{report.engine_name}' can never be published — committed results "
            f"require engine={_PUBLISHABLE_ENGINE} (plan Phase 4)"
        )
    for dataset in config.datasets:
        reasons.extend(_validate_dataset_completeness(report, dataset.source))
    for missing in payload.missing_typologies:
        reasons.append(f"curation found no real '{missing}' motif — a motif is never invented")
    typologies = sorted(motif.typology for motif in payload.motifs)
    if typologies != sorted(TYPOLOGIES):
        reasons.append(f"curated motifs must cover exactly {sorted(TYPOLOGIES)}, got {typologies}")
    if reasons:
        raise ValueError("run is not publishable:\n- " + "\n- ".join(reasons))


def _scan_redaction(content: str, artifact: str) -> None:
    """Fail closed when a committed byte stream carries a forbidden pattern."""
    for pattern in _REDACTION_FORBIDDEN:
        if pattern in content:
            raise ValueError(
                f"redaction scan failed for {artifact}: forbidden pattern "
                f"{pattern.encode('unicode_escape').decode('ascii')!r} present"
            )


def _isolation_phrase(delta: float) -> str:
    """The honesty rule: only a POSITIVE delta may be called a cost of isolation."""
    return "cost of isolation" if delta > 0 else "isolation delta"


@dataclass(frozen=True)
class _HeadlineRecords:
    """The full-data records the study headline + frontend hero are both derived from."""

    source: str
    arm_a: ArmMetrics
    arm_c_global: ArmMetrics
    delta_a_c_global: "ArmDelta"
    comparison: "ScopeComparison"


def _headline_records(report: StudyReport) -> _HeadlineRecords:
    """Select the full-data global records the headline + hero are derived from.

    Prefers a full-context dataset (the primary result); falls back to the first
    declared dataset so a run without one still resolves.
    """
    full_sources = [
        d.spec.source for d in report.datasets if d.spec.graph_context == _RESUME_DATASET_CONTEXT
    ]
    source = full_sources[0] if full_sources else report.datasets[0].spec.source
    arm_a = next(m for m in report.metrics if m.dataset_source == source and m.arm == "A")
    arm_c = next(
        m
        for m in report.metrics
        if m.dataset_source == source and m.arm == "C" and m.scope == "global"
    )
    delta = next(
        d
        for d in report.deltas
        if d.dataset_source == source
        and d.from_arm == "A"
        and d.to_arm == "C"
        and d.scope == "global"
    )
    comparison = next(c for c in report.comparisons if c.dataset_source == source)
    return _HeadlineRecords(
        source=source, arm_a=arm_a, arm_c_global=arm_c, delta_a_c_global=delta, comparison=comparison
    )


def _highlight_metrics(report: StudyReport) -> StudyHighlightMetrics:
    """Project the signed headline numbers the frontend hero renders (plan Phase 7)."""
    records = _headline_records(report)
    return StudyHighlightMetrics(
        dataset_source=records.source,
        arm_a_pr_auc=records.arm_a.pr_auc,
        arm_c_pr_auc=records.arm_c_global.pr_auc,
        arm_c_pr_auc_normalized=records.arm_c_global.pr_auc_normalized,
        arm_a_to_c_lift=records.delta_a_c_global.pr_auc_delta,
        arm_a_to_c_ci_lower=records.delta_a_c_global.interval.lower,
        arm_a_to_c_ci_upper=records.delta_a_c_global.interval.upper,
        isolation_delta_c=records.comparison.isolation_delta_c,
    )


def _resume_sentence(report: StudyReport) -> str:
    """The mechanically derived study headline (positive wording only when supported)."""
    records = _headline_records(report)
    source = records.source
    arm_a = records.arm_a
    arm_c = records.arm_c_global
    delta = records.delta_a_c_global
    if delta.pr_auc_delta > 0 and delta.interval.lower > 0:
        headline = (
            f"Adding complete GFP graph features moved holdout PR-AUC from {arm_a.pr_auc:.4f} "
            f"to {arm_c.pr_auc:.4f} on {source} (+{delta.pr_auc_delta:.4f}, 95% CI "
            f"[{delta.interval.lower:.4f}, {delta.interval.upper:.4f}])."
        )
    else:
        headline = (
            f"The GFP benchmark completed on {source} with no significant holdout PR-AUC lift "
            f"({arm_a.pr_auc:.4f} -> {arm_c.pr_auc:.4f}, 95% CI [{delta.interval.lower:.4f}, "
            f"{delta.interval.upper:.4f}])."
        )
    return headline + " Serving is deferred under strict tenant isolation (ADR-017)."


def _metrics_table(metrics: list[ArmMetrics]) -> list[str]:
    """One dataset's arm x scope metric table rows."""
    lines = [
        "| Arm | Scope | PR-AUC | Norm. lift | ROC-AUC | Brier | ECE | P@0.1% | R@0.1% | Min. F1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for metric in metrics:
        top = metric.top_k[0]
        lines.append(
            f"| {metric.arm} | {metric.scope} | {metric.pr_auc:.4f} "
            f"| {metric.pr_auc_normalized:.1f} | {metric.roc_auc:.4f} | {metric.brier:.5f} "
            f"| {metric.ece:.4f} | {top.precision:.3f} | {top.recall:.3f} "
            f"| {metric.minority_f1:.3f} |"
        )
    return lines


def render_markdown(report: StudyReport, visual: CuratedVisualData) -> str:
    """Render the committed Markdown SOLELY from the typed report + visual payload."""
    lines: list[str] = [
        "# GFP tenant-isolation study (offline; public synthetic data)",
        "",
        "> Generated from the committed study JSON — do not edit by hand. Offline-only:",
        "> no scope of these graph features may serve (ADR-017).",
        "",
        _resume_sentence(report),
        "",
        f"- Run: `{report.run_id}` | engine: {report.engine_name} {report.engine_version} "
        f"| seed: {report.seed}",
        f"- Protocol: `config/gfp-benchmark.yaml` (sha256 `{report.config_sha256[:12]}...`)",
        "- Libraries: "
        + ", ".join(
            f"{name} {version}" for name, version in sorted(report.library_versions.items())
        ),
        "",
        "## Datasets",
        "",
        "| Source | Context | Source rows | Context edges | Targets | Illicit ratio "
        "| Hash fraction |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in report.datasets:
        ratio = f"{item.illicit_ratio:.6f}" if item.illicit_ratio is not None else "n/a"
        fraction = item.node_hash_fraction or "n/a"
        lines.append(
            f"| {item.spec.source} | {item.spec.graph_context} | {item.source_row_count} "
            f"| {item.context_edge_count} | {item.target_count} | {ratio} | {fraction} |"
        )
    for item in report.datasets:
        source = item.spec.source
        lines += ["", f"## {source} — arm metrics", ""]
        lines += _metrics_table(_dataset_metrics(report, source))
    lines += [
        "",
        "## Arm deltas (paired bootstrap 95% CI)",
        "",
        "| Dataset | From | To | Scope | ΔPR-AUC | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for delta in report.deltas:
        lines.append(
            f"| {delta.dataset_source} | {delta.from_arm} | {delta.to_arm} | {delta.scope} "
            f"| {delta.pr_auc_delta:+.4f} "
            f"| [{delta.interval.lower:+.4f}, {delta.interval.upper:+.4f}] |"
        )
    lines += [
        "",
        "## Tenant isolation (signed)",
        "",
        "| Dataset | Δ B (global - per-tenant) | Δ C | Lost graph lift | Retained share |",
        "|---|---|---|---|---|",
    ]
    for comparison in report.comparisons:
        retained = (
            f"{comparison.retained_graph_lift:.3f}"
            if comparison.retained_graph_lift is not None
            else f"n/a ({comparison.retained_lift_note})"
        )
        lines.append(
            f"| {comparison.dataset_source} | {comparison.isolation_delta_b:+.4f} "
            f"| {comparison.isolation_delta_c:+.4f} | {comparison.lost_graph_lift:+.4f} "
            f"| {retained} |"
        )
        lines.append("")
        lines.append(
            f"For {comparison.dataset_source}, the Arm-C "
            f"{_isolation_phrase(comparison.isolation_delta_c)} "
            f"is {comparison.isolation_delta_c:+.4f} PR-AUC."
        )
    lines += [
        "",
        "## Curated visual",
        "",
        f"{len(visual.motifs)} motifs (report sha256 `{visual.report_sha256[:12]}...`): "
        + ", ".join(motif.typology for motif in visual.motifs)
        + ".",
        "",
        "## Disclosures",
        "",
    ]
    lines += [f"- {note}" for note in report.notes]
    return "\n".join(lines) + "\n"


def publish_run(
    run_dir: Path, *, config: GfpBenchmarkConfig, docs_dir: Path, frontend_json_path: Path
) -> PublishResult:
    """Validate one run, then atomically write the three committed artifacts.

    The frontend payload embeds SHA-256(report JSON) so the two committed JSONs are
    verifiably bound; `validate_published_artifacts` re-checks that binding.
    """
    report, payload = load_run(run_dir)
    validate_publishable(report, payload, config)
    report_json = _report_json(report) + "\n"
    report_sha256 = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
    visual = CuratedVisualData(
        report_sha256=report_sha256,
        metrics=_highlight_metrics(report),
        agency_names=payload.agency_names,
        motifs=payload.motifs,
    )
    frontend_json = (
        json.dumps(
            visual.model_dump(mode="json", by_alias=True), indent=_JSON_INDENT, sort_keys=True
        )
        + "\n"
    )
    markdown = render_markdown(report, visual)
    _scan_redaction(report_json, f"{REPORT_BASENAME}.json")
    _scan_redaction(frontend_json, frontend_json_path.name)
    _scan_redaction(markdown, f"{REPORT_BASENAME}.md")
    report_json_path = docs_dir / f"{REPORT_BASENAME}.json"
    report_markdown_path = docs_dir / f"{REPORT_BASENAME}.md"
    _atomic_write_text(report_json_path, report_json)
    _atomic_write_text(report_markdown_path, markdown)
    _atomic_write_text(frontend_json_path, frontend_json)
    validate_published_artifacts(report_json_path, frontend_json_path)
    return PublishResult(
        report_json_path=report_json_path,
        report_markdown_path=report_markdown_path,
        frontend_json_path=frontend_json_path,
        report_sha256=report_sha256,
    )


def validate_published_artifacts(report_json_path: Path, frontend_json_path: Path) -> None:
    """Assert the committed report + frontend JSONs are still hash-bound (no drift)."""
    report_bytes = report_json_path.read_bytes()
    visual = CuratedVisualData.model_validate_json(frontend_json_path.read_text(encoding="utf-8"))
    observed = hashlib.sha256(report_bytes).hexdigest()
    if observed != visual.report_sha256:
        raise ValueError(
            f"published artifacts drifted: {report_json_path.name} hashes to "
            f"{observed[:12]}... but {frontend_json_path.name} embeds "
            f"{visual.report_sha256[:12]}... — republish the run"
        )
