"""Behavioral tests for Kubernetes evidence acceptance, projections, and secret handling."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
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
    NodePoolSnapshot,
    PaidSessionEvidence,
    ScalingSample,
    ScalingSummary,
    WorkloadSnapshot,
    config_sha256,
    evidence_sha256,
    load_evidence,
    load_success_disclosure,
    validate_evidence,
)
from lib.k8s_demo.load import LoadSummary
from lib.k8s_demo.report import publish_evidence, render_markdown
from lib.k8s_demo.secrets import load_secret_sets, sync_secrets

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _report(**updates: object) -> HpaEvidenceReport:
    values: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime(2026, 9, 14, tzinfo=UTC),
        "platform": "kind",
        "run_id": "k8s-demo-0123456789abcdef",
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


def _paid_session() -> PaidSessionEvidence:
    return PaidSessionEvidence(
        execution_source="github-actions",
        execution_id="123456",
        manifest_sha256="c" * 64,
        image_digest=f"sha256:{'d' * 64}",
        node_pools=[NodePoolSnapshot(name="user", vm_size="Standard_D2as_v4", node_count=2)],
        elapsed_cluster_seconds=1800,
        projected_cost_usd=Decimal("0.79"),
    )


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


def test_valid_aks_evidence_uses_non_kind_context_and_docs_only(tmp_path: Path) -> None:
    cluster = ClusterFacts(
        name="fraudlens-aks-demo-aks",
        context="fraudlens-aks-demo-aks",
        kubernetes_version="v1.32.8",
        node_count=2,
        architectures=["amd64"],
    )
    report = _report(
        platform="aks",
        cluster=cluster,
        run_id="aks-demo-20260915-01",
        paid_session=_paid_session(),
        load=_report().load.model_copy(update={"mode": "authenticated"}),
        workload=_report().workload.model_copy(
            update={"image": f"ghcr.io/example/fraudlens-backend@sha256:{'d' * 64}"}
        ),
    )
    validate_evidence(report)
    paths = publish_evidence(report, root=tmp_path)
    assert [path.name for path in paths] == ["aks-hpa-scaling.json", "aks-hpa-scaling.md"]
    markdown = paths[1].read_text(encoding="utf-8")
    assert "Standard_D2as_v4" in markdown
    assert "Projected session cost | $0.79" in markdown
    with pytest.raises(EvidenceError, match="immutable digest"):
        validate_evidence(
            report.model_copy(
                update={"workload": report.workload.model_copy(update={"image": "mutable:tag"})}
            )
        )


def test_a_rate_limited_scaling_load_must_publish_the_rate_it_actually_served() -> None:
    """Scaling proven while the API refused 99.85% of requests is not a throughput result.

    Before release 0.5.0 the only bar was `succeeded > 0`, so an artifact could report a scale-out
    beside 1,147 of 783,498 served requests and read as a clean load test. Now the evidence either
    clears the served-share floor or states the measured counts in its own disclosures.
    """
    cluster = ClusterFacts(
        name="fraudlens-aks-demo-aks",
        context="fraudlens-aks-demo-aks",
        kubernetes_version="v1.32.8",
        node_count=2,
        architectures=["amd64"],
    )
    throttled = _report().load.model_copy(
        update={"mode": "authenticated", "requests": 783498, "succeeded": 1147, "failed": 782351}
    )
    report = _report(
        platform="aks",
        cluster=cluster,
        run_id="aks-demo-20260915-01",
        paid_session=_paid_session(),
        load=throttled,
        workload=_report().workload.model_copy(
            update={"image": f"ghcr.io/example/fraudlens-backend@sha256:{'d' * 64}"}
        ),
    )

    with pytest.raises(EvidenceError, match="without publishing the measured-rate disclosure"):
        validate_evidence(report)

    disclosure = load_success_disclosure(throttled)
    assert "1147 of 783498" in disclosure and "0.15%" in disclosure
    validate_evidence(report.model_copy(update={"disclosures": [*report.disclosures, disclosure]}))
    # A healthy served share needs no disclosure at all.
    validate_evidence(
        report.model_copy(
            update={"load": throttled.model_copy(update={"succeeded": 783498, "failed": 0})}
        )
    )


def test_the_published_aks_artifact_carries_its_own_load_caveat() -> None:
    """The committed evidence is the thing a reader sees; the caveat must live in it."""
    published = load_evidence(
        (_REPO_ROOT / "docs/reference/benchmarks/aks-hpa-scaling.json").read_text(encoding="utf-8")
    )

    assert load_success_disclosure(published.load) in published.disclosures
    validate_evidence(published)


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
    monkeypatch.setenv("FRAUDLENS_DEMO_AUTH_PASSWORD", "demo-password")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "role-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "llm-secret")
    groups = load_secret_sets()
    assert len(groups) == 2
    assert "postgresql://secret" not in repr(groups)


def test_secret_sync_applies_values_only_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("FRAUDLENS_DEMO_AUTH_PASSWORD", "demo-password")
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
    assert documents[0]["stringData"]["FRAUDLENS_DEMO_AUTH_PASSWORD"] == "demo-password"
    assert documents[1]["stringData"] == {"OPENROUTER_API_KEY": "llm-secret"}
