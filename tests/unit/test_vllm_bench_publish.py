"""Summary: Acceptance-gated atomic benchmark publication and binding tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Publications target isolated paths and never call GitHub or a provider.
"""

from __future__ import annotations

import json

import pytest
from vllm_bench_fakes import complete_benchmark

from lib.vllm_bench.config import load_config
from lib.vllm_bench.publish import publish_report, validate_published_artifacts
from lib.vllm_bench.report import build_report


def test_publish_installs_three_hash_bound_artifacts(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    result = publish_report(report, config, sandbox)
    assert result.report_json_path.is_file()
    assert result.report_markdown_path.is_file()
    assert result.frontend_json_path.is_file()
    assert (
        validate_published_artifacts(result.report_json_path, result.frontend_json_path, config)
        == report
    )
    frontend = json.loads(result.frontend_json_path.read_text())
    assert frontend["reportSha256"] == result.report_sha256


def test_publish_rejects_smoke_unmet_and_config_drift(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    with pytest.raises(ValueError, match="profile=full"):
        publish_report(report.model_copy(update={"profile": "smoke"}), config, sandbox)

    failed_checks = tuple(
        check.model_copy(update={"passed": False}) if check.name == "profile_full" else check
        for check in report.acceptance
    )
    failed = report.model_copy(
        update={
            "acceptance": failed_checks,
            "acceptance_met": False,
            "headline": "Acceptance NOT met (profile_full). " + report.headline,
        }
    )
    with pytest.raises(ValueError, match="acceptance is unmet"):
        publish_report(failed, config, sandbox)
    with pytest.raises(ValueError, match="NOT-met headline"):
        publish_report(
            failed.model_copy(update={"headline": report.headline}),
            config,
            sandbox,
            allow_unmet_acceptance=True,
        )
    assert publish_report(failed, config, sandbox, allow_unmet_acceptance=True).report_json_path

    with pytest.raises(ValueError, match="does not match"):
        publish_report(report.model_copy(update={"config_sha256": "b" * 64}), config, sandbox)


def test_publish_rolls_back_all_files_when_validation_fails(sandbox, monkeypatch) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    docs = sandbox / "docs/reference/benchmarks"
    frontend = sandbox / "frontend/src/data"
    docs.mkdir(parents=True)
    frontend.mkdir(parents=True)
    report_path = docs / "vllm-awq-sar-benchmark.json"
    markdown_path = docs / "vllm-awq-sar-benchmark.md"
    frontend_path = frontend / "vllm-awq-sar-benchmark.json"
    for path in (report_path, markdown_path, frontend_path):
        path.write_text("before")
    monkeypatch.setattr(
        "lib.vllm_bench.publish.validate_published_artifacts",
        lambda *_args: (_ for _ in ()).throw(ValueError("synthetic validation failure")),
    )
    with pytest.raises(ValueError, match="synthetic validation"):
        publish_report(report, config, sandbox)
    assert [path.read_text() for path in (report_path, markdown_path, frontend_path)] == [
        "before",
        "before",
        "before",
    ]


def test_published_binding_detects_frontend_tampering(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    result = publish_report(build_report(manifest, artifact, config), config, sandbox)
    payload = json.loads(result.frontend_json_path.read_text())
    payload["runId"] = "tampered"
    result.frontend_json_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="binding drifted"):
        validate_published_artifacts(result.report_json_path, result.frontend_json_path, config)


def test_published_binding_detects_markdown_tampering(sandbox) -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    result = publish_report(build_report(manifest, artifact, config), config, sandbox)
    result.report_markdown_path.write_text("tampered")
    with pytest.raises(ValueError, match="Markdown rendering drifted"):
        validate_published_artifacts(result.report_json_path, result.frontend_json_path, config)


def test_evidence_published_under_a_superseded_protocol_stays_hash_bound_to_it(sandbox) -> None:
    """Bumping the protocol must not orphan, or silently re-bless, already-published evidence."""
    config, artifact, manifest = complete_benchmark(load_config())
    report = build_report(manifest, artifact, config)
    published = publish_report(report, config, sandbox)
    superseded = config.model_copy(
        update={
            "protocol_version": "vllm-sar-bench-next",
            "protocol_lineage": {report.protocol_version: report.config_sha256},
        }
    )

    assert (
        validate_published_artifacts(
            published.report_json_path, published.frontend_json_path, superseded
        )
        == report
    )

    unrecorded = superseded.model_copy(update={"protocol_lineage": {}})
    with pytest.raises(ValueError, match="no recorded config hash"):
        validate_published_artifacts(
            published.report_json_path, published.frontend_json_path, unrecorded
        )

    wrong = superseded.model_copy(update={"protocol_lineage": {report.protocol_version: "0" * 64}})
    with pytest.raises(ValueError, match="config hash drifted"):
        validate_published_artifacts(
            published.report_json_path, published.frontend_json_path, wrong
        )
