"""Summary: Validated atomic publication and report/frontend hash binding.

Key classes:
- PublishResult: committed report paths and exact report SHA-256.

Key functions:
- publish_report: validate, redact, and atomically install docs and frontend artifacts.
- validate_published_artifacts: re-parse and verify the committed hash binding.

Notes:
- Publication is providerless and exposes aggregate public-synthetic evidence only.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.fulldata.config import FullDataConfig
from lib.fulldata.report import (
    FrontendFullDataProjection,
    FullDataStudyReport,
    frontend_projection,
    render_markdown,
)
from lib.study import canonical_json, install_bound_artifacts, scan_forbidden, sha256_hex

REPORT_BASENAME = "ibm-full-data-training"
_FRONTEND_FILE = Path("frontend/src/data/ibm-full-data-training.json")
_DOCS_DIRECTORY = Path("docs/reference/benchmarks")
_FORBIDDEN = ("/Users/", "/home/", "C:\\", ".local/", "Bearer ", "Authorization")


class PublishResult(BaseModel):
    """Committed full report, Markdown, browser projection, and binding hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_json_path: Path = Field(..., description="Committed typed report JSON.")
    report_markdown_path: Path = Field(..., description="Committed rendered Markdown.")
    frontend_json_path: Path = Field(..., description="Committed aggregate frontend JSON.")
    report_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Report byte hash.")


def _scan(content: str, artifact: str) -> None:
    scan_forbidden(content, artifact=artifact, forbidden=_FORBIDDEN)


def _validate_report(report: FullDataStudyReport, config: FullDataConfig) -> None:
    if report.manifest.config_sha256 != config.config_sha256:
        raise ValueError("full-data report does not match config/fulldata.yaml bytes")
    if report.manifest.application_candidate != config.application_candidate:
        raise ValueError("full-data report changed the pre-registered application candidate")
    if report.run_id != report.manifest.run_id:
        raise ValueError("full-data report run identity is inconsistent")


def publish_report(
    report: FullDataStudyReport, config: FullDataConfig, repo_root: Path
) -> PublishResult:
    """Publish a validated report and browser projection as one rollback-safe artifact set."""
    _validate_report(report, config)
    report_json = canonical_json(report)
    report_sha256 = sha256_hex(report_json)
    frontend_json = canonical_json(frontend_projection(report, report_sha256))
    markdown = render_markdown(report)
    _scan(report_json, f"{REPORT_BASENAME}.json")
    _scan(frontend_json, _FRONTEND_FILE.name)
    _scan(markdown, f"{REPORT_BASENAME}.md")
    report_path = repo_root / _DOCS_DIRECTORY / f"{REPORT_BASENAME}.json"
    markdown_path = repo_root / _DOCS_DIRECTORY / f"{REPORT_BASENAME}.md"
    frontend_path = repo_root / _FRONTEND_FILE

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
    report_path: Path, frontend_path: Path, config: FullDataConfig
) -> FullDataStudyReport:
    """Strictly parse committed artifacts and enforce config plus exact byte-hash binding."""
    report_bytes = report_path.read_bytes()
    frontend_text = frontend_path.read_text(encoding="utf-8")
    _scan(report_bytes.decode("utf-8"), report_path.name)
    _scan(frontend_text, frontend_path.name)
    report = FullDataStudyReport.model_validate_json(report_bytes)
    frontend = FrontendFullDataProjection.model_validate_json(frontend_text)
    _validate_report(report, config)
    if frontend.report_sha256 != sha256_hex(report_bytes) or frontend.run_id != report.run_id:
        raise ValueError("published full-data artifacts drifted; republish the completed run")
    return report
