"""Summary: Rollback-safe three-file publication and report/frontend SHA-256 validation.

Key classes:
- PublishResult: committed paths and exact report byte hash.

Key functions:
- publish_report: enforce full-profile acceptance and atomically install report artifacts.
- validate_published_artifacts: verify report, frontend, config, and byte-hash binding.

Notes:
- `allow_unmet_acceptance` preserves failed criteria and requires the mechanical NOT-met headline.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.study import canonical_json, install_bound_artifacts, scan_forbidden, sha256_hex
from lib.vllm_bench.config import VllmBenchConfig
from lib.vllm_bench.render import render_markdown
from lib.vllm_bench.report_models import FrontendVllmBenchData, VllmBenchReport

REPORT_BASENAME = "vllm-awq-sar-benchmark"
_DOCS = Path("docs/reference/benchmarks")
_FRONTEND = Path("frontend/src/data/vllm-awq-sar-benchmark.json")
_FORBIDDEN = ("/Users/", "/home/", "C:\\", ".local/", "Bearer ", "Authorization")


class PublishResult(BaseModel):
    """Committed report, Markdown, browser projection, and binding hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_json_path: Path = Field(..., description="Committed report JSON.")
    report_markdown_path: Path = Field(..., description="Committed report Markdown.")
    frontend_json_path: Path = Field(..., description="Committed browser projection.")
    report_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Report byte hash.")


def _frontend(report: VllmBenchReport, report_sha256: str) -> FrontendVllmBenchData:
    """Project aggregate report evidence for the later frontend phase."""
    return FrontendVllmBenchData(
        report_sha256=report_sha256,
        run_id=report.run_id,
        headline=report.headline,
        acceptance_met=report.acceptance_met,
        measured_cases=report.measured_cases,
        weight_memory_reduction=report.weight_memory_reduction,
        arms=report.arms,
        quality_deltas=report.quality_deltas,
    )


def _scan(content: str, artifact: str) -> None:
    scan_forbidden(content, artifact=artifact, forbidden=_FORBIDDEN)


def publish_report(
    report: VllmBenchReport,
    config: VllmBenchConfig,
    repo_root: Path,
    *,
    allow_unmet_acceptance: bool = False,
) -> PublishResult:
    """Enforce publish policy and atomically install JSON, Markdown, and browser data."""
    if report.profile != "full":
        raise ValueError("only profile=full benchmark reports may be published")
    if not report.acceptance_met and not allow_unmet_acceptance:
        raise ValueError("benchmark acceptance is unmet; pass --allow-unmet-acceptance to disclose")
    if not report.acceptance_met and "Acceptance NOT met" not in report.headline:
        raise ValueError("unmet acceptance publication requires a mechanical NOT-met headline")
    if report.config_sha256 != config.config_sha256:
        raise ValueError("benchmark report does not match config/vllm-bench.yaml")
    report_json = canonical_json(report)
    report_sha256 = sha256_hex(report_json)
    frontend_json = canonical_json(_frontend(report, report_sha256))
    markdown = render_markdown(report)
    for name, content in (
        (f"{REPORT_BASENAME}.json", report_json),
        (f"{REPORT_BASENAME}.md", markdown),
        (_FRONTEND.name, frontend_json),
    ):
        _scan(content, name)
    report_path = repo_root / _DOCS / f"{REPORT_BASENAME}.json"
    markdown_path = repo_root / _DOCS / f"{REPORT_BASENAME}.md"
    frontend_path = repo_root / _FRONTEND

    def validate_installed() -> None:
        validate_published_artifacts(report_path, frontend_path, config)

    install_bound_artifacts(
        {report_path: report_json, markdown_path: markdown, frontend_path: frontend_json},
        validate=validate_installed,
    )
    return PublishResult(
        report_json_path=report_path,
        report_markdown_path=markdown_path,
        frontend_json_path=frontend_path,
        report_sha256=report_sha256,
    )


def validate_published_artifacts(
    report_path: Path,
    frontend_path: Path,
    config: VllmBenchConfig,
) -> VllmBenchReport:
    """Strictly verify report, Markdown, frontend, config, and exact byte binding."""
    report_bytes = report_path.read_bytes()
    frontend_bytes = frontend_path.read_bytes()
    markdown_path = report_path.with_suffix(".md")
    markdown = markdown_path.read_text(encoding="utf-8")
    _scan(report_bytes.decode(), report_path.name)
    _scan(frontend_bytes.decode(), frontend_path.name)
    _scan(markdown, markdown_path.name)
    report = VllmBenchReport.model_validate_json(report_bytes)
    frontend = FrontendVllmBenchData.model_validate_json(frontend_bytes)
    if report.config_sha256 != config.config_sha256:
        raise ValueError("published benchmark report config hash drifted")
    if frontend.report_sha256 != sha256_hex(report_bytes) or frontend.run_id != report.run_id:
        raise ValueError("published benchmark frontend binding drifted")
    if markdown != render_markdown(report):
        raise ValueError("published benchmark Markdown rendering drifted")
    return report
