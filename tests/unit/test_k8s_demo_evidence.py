"""Behavioral tests for Kubernetes evidence acceptance, projections, and secret handling."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from lib.k8s_demo import secrets as secrets_module
from lib.k8s_demo.config import load_config
from lib.k8s_demo.evidence import (
    ClusterFacts,
    DurabilityEvidence,
    EvidenceError,
    HpaEvidenceReport,
    HpaSpecSnapshot,
    ScalingSample,
    ScalingSummary,
    WorkloadSnapshot,
    config_sha256,
    evidence_sha256,
    load_evidence,
    validate_evidence,
)
from lib.k8s_demo.load import LoadSummary
from lib.k8s_demo.report import publish_evidence, render_markdown
from lib.k8s_demo.secrets import load_secret_sets, sync_secrets


def _report(**updates: object) -> HpaEvidenceReport:
    values: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime(2026, 9, 14, tzinfo=UTC),
        "platform": "kind",
        "commit": "a" * 40,
        "config_sha256": "b" * 64,
        "cluster": ClusterFacts(
            name="demo",
            context="kind-demo",
            kubernetes_version="v1.32.8",
            node_count=2,
            architectures=["arm64"],
        ),
        "hpa": HpaSpecSnapshot(
            target="Deployment/fraudlens-api",
            min_replicas=1,
            max_replicas=5,
            cpu_target_percent=60,
            scale_down_stabilization_seconds=60,
        ),
        "workload": WorkloadSnapshot(
            image="fraudlens-backend:proof",
            cpu_request="100m",
            memory_request="512Mi",
            cpu_limit="1",
            memory_limit="1536Mi",
        ),
        "load": LoadSummary(
            mode="healthz",
            requests=100,
            succeeded=100,
            failed=0,
            duration_seconds=10,
            latency_p50_ms=1,
            latency_p95_ms=2,
        ),
        "samples": [
            ScalingSample(elapsed_seconds=0, replicas=1, desired_replicas=1, cpu_percent=10),
            ScalingSample(elapsed_seconds=30, replicas=5, desired_replicas=5, cpu_percent=95),
            ScalingSample(elapsed_seconds=150, replicas=1, desired_replicas=1, cpu_percent=None),
        ],
        "summary": ScalingSummary(
            replicas_min_observed=1,
            replicas_max_observed=5,
            seconds_to_first_scale_up=30,
            seconds_to_max_replicas=30,
            seconds_to_scale_back_to_min=60,
        ),
        "durability": DurabilityEvidence(
            worker_pod_deleted=True,
            runs_submitted=10,
            runs_completed=10,
            runs_completed_after_worker_kill=10,
            runs_failed=0,
            max_run_attempts=2,
        ),
        "disclosures": ["Synthetic local proof."],
    }
    values.update(updates)
    return HpaEvidenceReport.model_validate(values)


def test_valid_evidence_hashes_renders_and_publishes(tmp_path: Path) -> None:
    report = _report()
    validate_evidence(report)
    assert len(evidence_sha256(report)) == 64
    assert config_sha256(b"config") != config_sha256(b"changed")
    markdown = render_markdown(report)
    assert markdown.count("xychart-beta") == 2
    assert "1 → 5 → 1" in markdown
    paths = publish_evidence(report, root=tmp_path)
    assert all(path.is_file() for path in paths)
    docs_json = paths[0].read_text(encoding="utf-8")
    assert docs_json == paths[2].read_text(encoding="utf-8")
    assert load_evidence(docs_json) == report


@pytest.mark.parametrize(
    "update",
    [
        {"platform": "aks"},
        {
            "cluster": ClusterFacts(
                name="demo",
                context="wrong",
                kubernetes_version="v1",
                node_count=1,
                architectures=["amd64"],
            )
        },
        {
            "summary": ScalingSummary(
                replicas_min_observed=2,
                replicas_max_observed=4,
                seconds_to_first_scale_up=None,
                seconds_to_max_replicas=None,
                seconds_to_scale_back_to_min=None,
            )
        },
        {
            "summary": ScalingSummary(
                replicas_min_observed=0,
                replicas_max_observed=5,
                seconds_to_first_scale_up=30,
                seconds_to_max_replicas=30,
                seconds_to_scale_back_to_min=60,
            )
        },
        {
            "durability": DurabilityEvidence(
                worker_pod_deleted=False,
                runs_submitted=0,
                runs_completed=0,
                runs_completed_after_worker_kill=0,
                runs_failed=1,
                max_run_attempts=0,
            )
        },
        {
            "durability": DurabilityEvidence(
                worker_pod_deleted=True,
                runs_submitted=2,
                runs_completed=2,
                runs_completed_after_worker_kill=2,
                runs_failed=0,
                max_run_attempts=1,
            )
        },
        {
            "durability": DurabilityEvidence(
                worker_pod_deleted=True,
                runs_submitted=2,
                runs_completed=1,
                runs_completed_after_worker_kill=1,
                runs_failed=0,
                max_run_attempts=2,
            )
        },
    ],
)
def test_evidence_rejects_each_missing_proof(update: dict[str, object]) -> None:
    with pytest.raises(EvidenceError):
        validate_evidence(_report(**update))


def test_publish_redaction_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="redaction"):
        publish_evidence(_report(disclosures=["unsafe /Users/example path"]), root=tmp_path)


def test_secret_loading_requires_core_keys_and_redacts_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError, match="required secret keys"):
        load_secret_sets()
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "role-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "llm-secret")
    groups = load_secret_sets()
    assert len(groups) == 2
    assert "postgresql://secret" not in repr(groups)


def test_secret_sync_applies_values_only_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "role-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "llm-secret")
    applied: list[str] = []

    class FakeKubectl:
        def __init__(self, _config: object) -> None:
            pass

        def apply(self, manifest: str, *, platform: str, confirmed: bool) -> None:
            assert platform == "aks"
            assert confirmed is True
            applied.append(manifest)

    monkeypatch.setattr(secrets_module, "Kubectl", FakeKubectl)
    names = sync_secrets(load_config(), confirmed=True)
    assert names == ["fraudlens-backend-secrets", "fraudlens-llm-secrets"]
    documents = [yaml.safe_load(item) for item in applied]
    assert documents[0]["stringData"]["DATABASE_URL"] == "postgresql://secret"
    assert documents[1]["stringData"] == {"OPENROUTER_API_KEY": "llm-secret"}
