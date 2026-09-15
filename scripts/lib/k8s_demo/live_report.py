"""Summary: Assemble Kubernetes scaling and durability evidence from live cluster facts.

Key classes:
- (none)

Key functions:
- build_live_report: bind live cluster, HPA, workload, scaling, and durability observations.

Notes:
- The caller supplies its command and Kubectl dependencies to preserve CLI monkeypatch seams.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from lib.k8s_demo.config import K8sDemoConfig
from lib.k8s_demo.evidence import (
    ClusterFacts,
    DurabilityEvidence,
    HpaEvidenceReport,
    HpaSpecSnapshot,
    ScalingSample,
    ScalingSummary,
    WorkloadSnapshot,
    config_sha256,
)
from lib.k8s_demo.kubectl import CommandResult, Kubectl
from lib.k8s_demo.load import LoadSummary
from lib.k8s_demo.render import Platform

__all__ = ["build_live_report"]


def build_live_report(  # noqa: PLR0913 - binds every measured proof component explicitly.
    config: K8sDemoConfig,
    samples: list[ScalingSample],
    load: LoadSummary,
    load_finished_seconds: int,
    durability: DurabilityEvidence,
    *,
    platform: Platform,
    generated_at: datetime,
    config_path: Path,
    kubectl_factory: Callable[[K8sDemoConfig], Kubectl],
    command_runner: Callable[[list[str]], CommandResult],
) -> HpaEvidenceReport:
    """Read live facts and return their immutable evidence envelope."""
    kubectl = kubectl_factory(config)
    version = json.loads(kubectl.run(["version", "-o", "json"]).stdout)
    nodes = json.loads(kubectl.run(["get", "nodes", "-o", "json"]).stdout)
    hpa_document = json.loads(kubectl.get_json("hpa/fraudlens-api"))
    spec = hpa_document["spec"]
    cpu_metric = next(item for item in spec["metrics"] if item["resource"]["name"] == "cpu")
    workload = kubectl.deployment_observation()
    minimum = spec["minReplicas"]
    maximum = spec["maxReplicas"]
    first_up = next(
        (sample.elapsed_seconds for sample in samples if sample.replicas > minimum), None
    )
    first_max = next(
        (sample.elapsed_seconds for sample in samples if sample.replicas >= maximum), None
    )
    scale_back = next(
        (
            sample.elapsed_seconds - load_finished_seconds
            for sample in samples
            if sample.elapsed_seconds >= load_finished_seconds and sample.replicas <= minimum
        ),
        None,
    )
    commit = command_runner(["git", "rev-parse", "HEAD"]).stdout.strip()
    context = kubectl.current_context()
    is_kind = platform == "kind"
    return HpaEvidenceReport(
        schema_version="1.0",
        generated_at=generated_at,
        platform=platform,
        commit=commit,
        config_sha256=config_sha256(config_path.read_bytes()),
        cluster=ClusterFacts(
            name=config.cluster_name if is_kind else context,
            context=context,
            kubernetes_version=version["serverVersion"]["gitVersion"],
            node_count=len(nodes["items"]),
            architectures=sorted(
                {item["status"]["nodeInfo"]["architecture"] for item in nodes["items"]}
            ),
        ),
        hpa=HpaSpecSnapshot(
            target=f"{spec['scaleTargetRef']['kind']}/{spec['scaleTargetRef']['name']}",
            min_replicas=minimum,
            max_replicas=maximum,
            cpu_target_percent=cpu_metric["resource"]["target"]["averageUtilization"],
            scale_down_stabilization_seconds=spec["behavior"]["scaleDown"][
                "stabilizationWindowSeconds"
            ],
        ),
        workload=WorkloadSnapshot(**workload.model_dump()),
        load=load,
        samples=samples,
        summary=ScalingSummary(
            replicas_min_observed=min(sample.replicas for sample in samples),
            replicas_max_observed=max(sample.replicas for sample in samples),
            seconds_to_first_scale_up=first_up,
            seconds_to_max_replicas=first_max,
            seconds_to_scale_back_to_min=scale_back,
        ),
        durability=durability,
        disclosures=[
            (
                "kind uses kindnet, which does not enforce NetworkPolicy; enforcement is "
                "structural here and runs through Cilium on AKS."
                if is_kind
                else "AKS uses Azure CNI Overlay with the Cilium data plane and network policy."
            ),
            "The durability pass uses synthetic transactions and the keyless mock SAR provider; "
            "it measures recovery, not production model latency."
            if is_kind
            else "The durability pass uses synthetic transactions; it measures recovery, not "
            "the separate GPU benchmark's model latency.",
            "This is a zero-cost local execution. No Azure or RunPod resources were created."
            if is_kind
            else "This is paid AKS evidence and must carry its resource-session ledger record.",
        ],
    )
