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
import os
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from lib.k8s_demo.config import K8sDemoConfig
from lib.k8s_demo.evidence import (
    ClusterFacts,
    DurabilityEvidence,
    HpaEvidenceReport,
    HpaSpecSnapshot,
    NodePoolSnapshot,
    PaidSessionEvidence,
    ScalingSample,
    ScalingSummary,
    WorkloadSnapshot,
    config_sha256,
)
from lib.k8s_demo.kubectl import CommandResult, Kubectl
from lib.k8s_demo.load import LoadSummary
from lib.k8s_demo.render import Platform

__all__ = ["build_live_report"]


def _paid_session_evidence(
    nodes: list[dict[str, object]], generated_at: datetime
) -> tuple[str, PaidSessionEvidence]:
    """Build AKS-only evidence from governed workflow or local-session inputs."""
    required = {
        name: os.environ.get(name, "")
        for name in (
            "AKS_RUN_ID",
            "AKS_SESSION_STARTED_AT",
            "AKS_MANIFEST_SHA256",
            "AKS_IMAGE_DIGEST",
            "AKS_PROJECTED_COST_USD",
        )
    }
    if missing := sorted(name for name, value in required.items() if not value):
        raise ValueError(f"AKS evidence environment lacks {len(missing)} required value(s)")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "")
    local_execution_id = os.environ.get("AKS_EXECUTION_ID", "")
    execution_source = "github-actions" if github_run_id else "local"
    execution_id = github_run_id or local_execution_id
    if not execution_id:
        raise ValueError("AKS evidence environment lacks an execution identifier")
    started_at = datetime.fromisoformat(required["AKS_SESSION_STARTED_AT"].replace("Z", "+00:00"))
    pools: dict[tuple[str, str], int] = {}
    for node in nodes:
        metadata = node.get("metadata", {}) if isinstance(node, dict) else {}
        labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
        if not isinstance(labels, dict):
            continue
        name = str(labels.get("agentpool", "unknown"))
        vm_size = str(labels.get("node.kubernetes.io/instance-type", "unknown"))
        pools[(name, vm_size)] = pools.get((name, vm_size), 0) + 1
    return required["AKS_RUN_ID"], PaidSessionEvidence(
        execution_source=execution_source,
        execution_id=execution_id,
        manifest_sha256=required["AKS_MANIFEST_SHA256"],
        image_digest=required["AKS_IMAGE_DIGEST"],
        node_pools=[
            NodePoolSnapshot(name=name, vm_size=vm_size, node_count=count)
            for (name, vm_size), count in sorted(pools.items())
        ],
        elapsed_cluster_seconds=max(1, round((generated_at - started_at).total_seconds())),
        projected_cost_usd=Decimal(required["AKS_PROJECTED_COST_USD"]),
    )


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
    run_id, paid_session = (
        (None, None) if is_kind else _paid_session_evidence(nodes["items"], generated_at)
    )
    paid_disclosures = (
        []
        if paid_session is None
        else [
            f"Paid session {run_id} ran through the governed "
            f"{paid_session.execution_source} execution {paid_session.execution_id}.",
            "The user pool uses Standard_D2as_v4 instead of the ADR-021 "
            "Standard_D2as_v5 shape because the DASv5 family quota is zero.",
        ]
    )
    return HpaEvidenceReport(
        schema_version="1.1",
        generated_at=generated_at,
        platform=platform,
        commit=commit,
        config_sha256=config_sha256(config_path.read_bytes()),
        run_id=run_id,
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
        paid_session=paid_session,
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
            *paid_disclosures,
        ],
    )
