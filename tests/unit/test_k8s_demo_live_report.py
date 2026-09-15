"""Behavioral coverage for live Kubernetes evidence assembly and facade dependency seams."""

from __future__ import annotations

import json

import pytest

import k8s_demo
from lib.k8s_demo.config import K8sDemoConfig, load_config
from lib.k8s_demo.evidence import DurabilityEvidence, ScalingSample
from lib.k8s_demo.kubectl import CommandResult, DeploymentObservation
from lib.k8s_demo.load import LoadSummary


class FactsKubectl:
    """Minimal live-fact reader used to prove the CLI's injected dependencies."""

    def __init__(self, config: K8sDemoConfig) -> None:
        self.config = config

    def run(self, args: list[str], **_kwargs: object) -> CommandResult:
        if args == ["version", "-o", "json"]:
            stdout = '{"serverVersion":{"gitVersion":"v1.33.1"}}'
        else:
            stdout = '{"items":[{"status":{"nodeInfo":{"architecture":"arm64"}}}]}'
        return CommandResult(returncode=0, stdout=stdout, stderr="")

    def get_json(self, _resource: str) -> str:
        return json.dumps(
            {
                "spec": {
                    "minReplicas": 1,
                    "maxReplicas": 5,
                    "scaleTargetRef": {"kind": "Deployment", "name": "fraudlens-api"},
                    "metrics": [
                        {
                            "resource": {
                                "name": "cpu",
                                "target": {"averageUtilization": 60},
                            }
                        }
                    ],
                    "behavior": {"scaleDown": {"stabilizationWindowSeconds": 30}},
                }
            }
        )

    def deployment_observation(self) -> DeploymentObservation:
        return DeploymentObservation(
            image="fraudlens:test",
            cpu_request="100m",
            memory_request="256Mi",
            cpu_limit="500m",
            memory_limit="512Mi",
        )

    def current_context(self) -> str:
        return "kind-fraudlens-demo"


def test_live_report_reads_cluster_facts_through_facade_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(k8s_demo, "Kubectl", FactsKubectl)
    monkeypatch.setattr(
        k8s_demo,
        "run_command",
        lambda _args: CommandResult(returncode=0, stdout="a" * 40, stderr=""),
    )
    samples = [
        ScalingSample(elapsed_seconds=0, replicas=1, desired_replicas=1, cpu_percent=10),
        ScalingSample(elapsed_seconds=10, replicas=5, desired_replicas=5, cpu_percent=90),
        ScalingSample(elapsed_seconds=20, replicas=1, desired_replicas=1, cpu_percent=5),
    ]
    load = LoadSummary(
        mode="healthz",
        requests=100,
        succeeded=100,
        failed=0,
        duration_seconds=15,
        latency_p50_ms=1,
        latency_p95_ms=2,
    )
    durability = DurabilityEvidence(
        worker_pod_deleted=True,
        runs_submitted=100,
        runs_completed=100,
        runs_completed_after_worker_kill=100,
        runs_failed=0,
        max_run_attempts=2,
    )
    report = k8s_demo._live_report(load_config(), samples, load, 15, durability)
    assert report.cluster.node_count == 1
    assert report.summary.seconds_to_max_replicas == 10
    assert report.summary.seconds_to_scale_back_to_min == 5
    assert report.workload.image == "fraudlens:test"
