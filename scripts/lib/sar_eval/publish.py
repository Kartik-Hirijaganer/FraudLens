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

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.sar_eval.report import FrontendStudyData, SarEvalStudyReport, frontend_projection

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
    value = model.model_dump(mode="json", by_alias=True)
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _scan(content: str, name: str) -> None:
    for forbidden in _FORBIDDEN:
        if forbidden in content:
            raise ValueError(f"redaction scan failed for {name}: forbidden content present")


def _sidecar(path: Path, kind: str) -> Path:
    return path.with_name(f".{path.name}.sar-eval-{kind}")


def _validate_contents(report_bytes: bytes, frontend_text: str) -> SarEvalStudyReport:
    report = SarEvalStudyReport.model_validate_json(report_bytes)
    frontend = FrontendStudyData.model_validate_json(frontend_text)
    observed = hashlib.sha256(report_bytes).hexdigest()
    if frontend.report_sha256 != observed:
        raise ValueError("published SAR evaluation artifacts drifted; republish the completed run")
    return report


def _publish_pair(files: tuple[tuple[Path, str], tuple[Path, str]]) -> None:
    """Install two staged files and restore the prior pair if either replacement fails."""
    states = tuple(
        (target, _sidecar(target, "stage"), _sidecar(target, "backup"), target.exists())
        for target, _content in files
    )
    sidecars = [path for _target, staged, backup, _exists in states for path in (staged, backup)]
    if any(path.exists() for path in sidecars):
        raise RuntimeError("stale SAR evaluation publication sidecar exists")
    for (target, content), (_same_target, staged, _backup, _exists) in zip(
        files, states, strict=True
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(content, encoding="utf-8")

    backed_up: set[Path] = set()
    installed: set[Path] = set()
    try:
        for target, staged, backup, existed in states:
            if existed:
                os.replace(target, backup)
                backed_up.add(target)
            os.replace(staged, target)
            installed.add(target)
        validate_published_artifacts(files[0][0], files[1][0])
    except Exception:
        for target, _staged, backup, _existed in reversed(states):
            if target in installed and target.exists():
                target.unlink()
            if target in backed_up and backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for _target, staged, _backup, _existed in states:
            staged.unlink(missing_ok=True)
    for _target, _staged, backup, _existed in states:
        backup.unlink(missing_ok=True)


def publish_report(
    report: SarEvalStudyReport,
    *,
    docs_dir: Path,
    frontend_json_path: Path,
) -> PublishResult:
    """Atomically publish a complete report and its hash-bound frontend projection."""
    report_json = _json(report)
    report_sha256 = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
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
