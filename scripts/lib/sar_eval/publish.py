"""Summary: Validated atomic publication and SHA-256 binding for the SAR evaluation.
The full quote-level report is committed under docs/reference/benchmarks while the
aggregate-only frontend projection embeds the exact report byte hash; both are strictly
parsed again after writing so incomplete, tampered, or sensitive artifacts fail closed.

Key classes:
- PublishResult: committed artifact paths and binding hash.

Key functions:
- publish_report: validate, redact, atomically write, and bind both JSON artifacts.
- validate_published_artifacts: recompute and enforce the binding.

Notes:
- Publication performs no API or provider access.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.sar_eval.report import FrontendStudyData, SarEvalStudyReport, frontend_projection
from lib.study.artifacts import canonical_json, install_bound_artifacts
from lib.study.binding import sha256_hex
from lib.study.redaction import scan_forbidden

REPORT_BASENAME = "sar-multi-agent-study"
_FORBIDDEN = ("/Users/", "/home/", "C:\\", ".local/", "Bearer ", "Authorization", "accessToken")


class PublishResult(BaseModel):
    """Committed paths and the full report hash embedded by the frontend projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_json_path: Path = Field(..., description="Committed full documentation report.")
    frontend_json_path: Path = Field(..., description="Committed browser-safe projection.")
    report_sha256: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="Exact documentation report byte hash."
    )


def _json(model: BaseModel) -> str:
    return canonical_json(model)


def _scan(content: str, name: str) -> None:
    scan_forbidden(content, artifact=name, forbidden=_FORBIDDEN)


def _validate_contents(report_bytes: bytes, frontend_text: str) -> SarEvalStudyReport:
    report = SarEvalStudyReport.model_validate_json(report_bytes)
    frontend = FrontendStudyData.model_validate_json(frontend_text)
    observed = sha256_hex(report_bytes)
    if frontend.report_sha256 != observed:
        raise ValueError("published SAR evaluation artifacts drifted; republish the completed run")
    return report


def _publish_pair(files: tuple[tuple[Path, str], tuple[Path, str]]) -> None:
    """Install two staged files and restore the prior pair if either replacement fails."""

    def validate_pair() -> None:
        validate_published_artifacts(files[0][0], files[1][0])

    install_bound_artifacts(
        dict(files),
        validate=validate_pair,
    )


def publish_report(
    report: SarEvalStudyReport,
    *,
    docs_dir: Path,
    frontend_json_path: Path,
) -> PublishResult:
    """Atomically publish a complete report and its hash-bound frontend projection."""
    report_json = _json(report)
    report_sha256 = sha256_hex(report_json)
    frontend = frontend_projection(report, report_sha256)
    frontend_json = _json(frontend)
    _scan(report_json, f"{REPORT_BASENAME}.json")
    _scan(frontend_json, frontend_json_path.name)
    report_path = docs_dir / f"{REPORT_BASENAME}.json"
    _validate_contents(report_json.encode("utf-8"), frontend_json)
    _publish_pair(((report_path, report_json), (frontend_json_path, frontend_json)))
    return PublishResult(
        report_json_path=report_path,
        frontend_json_path=frontend_json_path,
        report_sha256=report_sha256,
    )


def validate_published_artifacts(report_path: Path, frontend_path: Path) -> SarEvalStudyReport:
    """Strictly parse both artifacts and assert the frontend embeds the report byte hash."""
    report_bytes = report_path.read_bytes()
    frontend_text = frontend_path.read_text(encoding="utf-8")
    _scan(report_bytes.decode("utf-8"), report_path.name)
    _scan(frontend_text, frontend_path.name)
    return _validate_contents(report_bytes, frontend_text)
