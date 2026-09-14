"""Summary: Safe JSON/Markdown/frontend projections for Kubernetes HPA and durability evidence.

Key classes:
- (none)

Key functions:
- render_markdown: render the evidence summary, sample table, and Mermaid charts.
- publish_evidence: validate, redact-scan, and atomically write all three committed projections.

Notes:
- Markdown is a projection of the JSON source and embeds its SHA-256 for drift detection.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lib.k8s_demo.evidence import HpaEvidenceReport, evidence_sha256, validate_evidence
from lib.study.redaction import scan_forbidden


def _chart(report: HpaEvidenceReport, field: str, title: str, y_label: str) -> list[str]:
    """Render one Mermaid xychart-beta from sampled evidence."""
    x_values = ", ".join(str(sample.elapsed_seconds) for sample in report.samples)
    values = [getattr(sample, field) for sample in report.samples]
    y_values = ", ".join(str(value or 0) for value in values)
    upper = max(1, *(value or 0 for value in values))
    return [
        "```mermaid",
        "xychart-beta",
        f'    title "{title}"',
        f'    x-axis "Elapsed seconds" [{x_values}]',
        f'    y-axis "{y_label}" 0 --> {upper}',
        f"    line [{y_values}]",
        "```",
    ]


def render_markdown(report: HpaEvidenceReport) -> str:
    """Render a concise human projection from validated machine evidence."""
    validate_evidence(report)
    digest = evidence_sha256(report)
    lines = [
        "# Kubernetes HPA and durable-worker evidence",
        "",
        f"Evidence SHA-256: `{digest}`",
        "",
        f"Measured commit: `{report.commit}` on `{report.platform}` / "
        f"`{report.cluster.kubernetes_version}`.",
        "",
        "| Result | Observed |",
        "|---|---:|",
        "| API replica range | "
        f"{report.summary.replicas_min_observed} → "
        f"{report.summary.replicas_max_observed} → {report.hpa.min_replicas} |",
        f"| First scale-up | {report.summary.seconds_to_first_scale_up} s |",
        f"| Scale-back to minimum | {report.summary.seconds_to_scale_back_to_min} s |",
        f"| Health requests succeeded | {report.load.succeeded} / {report.load.requests} |",
        "| Durable runs completed | "
        f"{report.durability.runs_completed} / {report.durability.runs_submitted} |",
        f"| Max worker attempt | {report.durability.max_run_attempts} |",
        "",
        *_chart(report, "replicas", "API replicas under load", "Replicas"),
        "",
        *_chart(report, "cpu_percent", "API CPU utilization", "CPU percent"),
        "",
        "## Samples",
        "",
        "| Elapsed (s) | Current replicas | Desired replicas | CPU % |",
        "|---:|---:|---:|---:|",
    ]
    for sample in report.samples:
        cpu = "—" if sample.cpu_percent is None else str(sample.cpu_percent)
        lines.append(
            f"| {sample.elapsed_seconds} | {sample.replicas} | {sample.desired_replicas} | {cpu} |"
        )
    lines.extend(["", "## Disclosures", ""])
    lines.extend(f"- {item}" for item in report.disclosures)
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    """Replace one evidence projection atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_evidence(report: HpaEvidenceReport, *, root: Path) -> list[Path]:
    """Validate and write canonical JSON, Markdown, and the frontend JSON projection."""
    validate_evidence(report)
    json_content = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    scan_forbidden(json_content, artifact="Kubernetes evidence JSON")
    scan_forbidden(markdown, artifact="Kubernetes evidence Markdown")
    paths = [
        root / "docs/reference/benchmarks/k8s-hpa-scaling.json",
        root / "docs/reference/benchmarks/k8s-hpa-scaling.md",
        root / "frontend/src/data/k8s-hpa-scaling.json",
    ]
    for path, content in zip(paths, (json_content, markdown, json_content), strict=True):
        _atomic_write(path, content)
    return paths
