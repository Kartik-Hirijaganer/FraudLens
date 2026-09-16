"""Summary: Hash-bound Pydantic evidence contract and acceptance validation for the kind proof.

Key classes:
- EvidenceError: acceptance failure with no sensitive evidence content.
- ClusterFacts: immutable local cluster identity and architecture facts.
- HpaSpecSnapshot: autoscaling target and behavior captured from the live object.
- ScalingSample: one time-series observation of replicas and CPU.
- ScalingSummary: derived scale-up and convergence timings.
- DurabilityEvidence: submitted/completed counts around the deliberate worker deletion.
- WorkloadSnapshot: measured API image and resource envelope.
- NodePoolSnapshot: redacted AKS pool shape observed from node labels.
- PaidSessionEvidence: paid-run lineage, hashes, lifetime, and projected cost.
- HpaEvidenceReport: complete publishable Phase 9 evidence envelope.

Key functions:
- validate_evidence: fail closed unless scaling and durability acceptance are proven.
- evidence_sha256: stable content hash used to bind the Markdown projection.
- config_sha256: hash the exact committed harness configuration.
- load_evidence: parse JSON through the strict evidence model.

Notes:
- No transaction fields, request bodies, credentials, or host paths are represented here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.k8s_demo.load import LoadSummary

_MIN_RECOVERY_ATTEMPTS = 2


class EvidenceError(RuntimeError):
    """Raised when observed evidence does not meet the frozen Phase 9 acceptance contract."""


class ClusterFacts(BaseModel):
    """Safe facts that identify the local Kubernetes execution environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="kind cluster name.")
    context: str = Field(..., description="kubectl context used for every mutation.")
    kubernetes_version: str = Field(..., description="Live Kubernetes Git version.")
    node_count: int = Field(..., ge=1, description="Cluster node count.")
    architectures: list[str] = Field(..., min_length=1, description="Unique node architectures.")


class HpaSpecSnapshot(BaseModel):
    """Autoscaler policy read from the live object rather than copied from config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(..., description="Scale target kind/name.")
    min_replicas: int = Field(..., ge=1, description="Minimum API replicas.")
    max_replicas: int = Field(..., ge=1, description="Maximum API replicas.")
    cpu_target_percent: int = Field(..., ge=1, le=100, description="CPU utilization target.")
    scale_down_stabilization_seconds: int = Field(
        ..., ge=1, description="Configured scale-down stabilization window."
    )


class WorkloadSnapshot(BaseModel):
    """Image and resources used by the measured API Deployment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image: str = Field(..., description="Measured API image.")
    cpu_request: str = Field(..., description="API CPU request.")
    memory_request: str = Field(..., description="API memory request.")
    cpu_limit: str = Field(..., description="API CPU limit.")
    memory_limit: str = Field(..., description="API memory limit.")


class NodePoolSnapshot(BaseModel):
    """Redacted AKS node-pool shape captured from live node labels."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1, description="AKS agent-pool name.")
    vm_size: str = Field(..., min_length=1, description="Azure VM size reported by Kubernetes.")
    node_count: int = Field(..., ge=1, description="Observed ready nodes in this pool.")


class PaidSessionEvidence(BaseModel):
    """Publishable, non-sensitive identity and cost facts for one paid AKS session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_source: Literal["github-actions", "local"] = Field(
        ..., description="Governed execution surface used for the paid session."
    )
    execution_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        description="Non-sensitive workflow or local execution identifier.",
    )
    manifest_sha256: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="Hash of the rendered AKS manifest."
    )
    image_digest: str = Field(
        ..., pattern=r"^sha256:[0-9a-f]{64}$", description="Immutable deployed image digest."
    )
    node_pools: list[NodePoolSnapshot] = Field(
        ..., min_length=1, description="Observed AKS node-pool shapes."
    )
    elapsed_cluster_seconds: int = Field(
        ..., ge=1, description="Elapsed billable cluster lifetime at evidence capture."
    )
    projected_cost_usd: Decimal = Field(
        ..., ge=0, le=5, description="Session cost projection under the approved ceiling."
    )


class ScalingSample(BaseModel):
    """One elapsed-time HPA sample."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    elapsed_seconds: int = Field(..., ge=0, description="Seconds since load start.")
    replicas: int = Field(..., ge=0, description="Observed current replicas.")
    desired_replicas: int = Field(..., ge=0, description="Observed desired replicas.")
    cpu_percent: int | None = Field(default=None, ge=0, description="Observed CPU utilization.")


class ScalingSummary(BaseModel):
    """Derived scaling bounds and response times."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replicas_min_observed: int = Field(..., ge=0, description="Minimum observed replicas.")
    replicas_max_observed: int = Field(..., ge=0, description="Maximum observed replicas.")
    seconds_to_first_scale_up: int | None = Field(
        default=None, ge=0, description="Time from load start to replicas above minimum."
    )
    seconds_to_max_replicas: int | None = Field(
        default=None, ge=0, description="Time from load start to configured maximum."
    )
    seconds_to_scale_back_to_min: int | None = Field(
        default=None, ge=0, description="Time from load completion to minimum replicas."
    )


class DurabilityEvidence(BaseModel):
    """Outcome of deleting a worker while persisted runs are pending or executing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_pod_deleted: bool = Field(..., description="Whether the harness deleted a worker pod.")
    runs_submitted: int = Field(..., ge=0, description="Runs accepted before deletion.")
    runs_completed: int = Field(..., ge=0, description="Runs completed after recovery.")
    runs_completed_after_worker_kill: int = Field(
        ..., ge=0, description="Completions observed after the deliberate deletion."
    )
    runs_failed: int = Field(..., ge=0, description="Terminally failed runs.")
    max_run_attempts: int = Field(..., ge=0, description="Maximum observed fencing attempt.")


class HpaEvidenceReport(BaseModel):
    """Complete, immutable evidence envelope for the local Kubernetes proof."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(..., description="Evidence schema revision.")
    generated_at: datetime = Field(..., description="UTC evidence generation timestamp.")
    platform: Literal["kind", "aks"] = Field(..., description="Measured Kubernetes platform.")
    commit: str = Field(..., pattern=r"^[0-9a-f]{40}$", description="Measured git commit.")
    config_sha256: str = Field(
        ..., pattern=r"^[0-9a-f]{64}$", description="Hash of config/k8s-demo.yaml."
    )
    run_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9-]+$", description="Paid-session ledger run id."
    )
    cluster: ClusterFacts = Field(..., description="Live cluster facts.")
    hpa: HpaSpecSnapshot = Field(..., description="Live HPA policy snapshot.")
    workload: WorkloadSnapshot = Field(..., description="Live API workload snapshot.")
    load: LoadSummary = Field(..., description="Health-load request summary.")
    samples: list[ScalingSample] = Field(..., min_length=2, description="Scaling time series.")
    summary: ScalingSummary = Field(..., description="Derived scaling acceptance values.")
    durability: DurabilityEvidence = Field(..., description="Worker recovery outcome.")
    paid_session: PaidSessionEvidence | None = Field(
        default=None, description="AKS-only paid-session provenance and cost evidence."
    )
    disclosures: list[str] = Field(..., min_length=1, description="Material scope limitations.")


def evidence_sha256(report: HpaEvidenceReport) -> str:
    """Return the stable SHA-256 of the canonical JSON evidence payload."""
    payload = report.model_dump_json(by_alias=True, exclude_none=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def config_sha256(content: bytes) -> str:
    """Hash the exact committed config bytes measured by the run."""
    return hashlib.sha256(content).hexdigest()


def validate_evidence(report: HpaEvidenceReport) -> None:
    """Fail unless the report proves full HPA scale-out/convergence and zero lost runs."""
    failures: list[str] = []
    if report.platform == "kind" and report.cluster.context != f"kind-{report.cluster.name}":
        failures.append("cluster context does not match its kind name")
    if report.platform == "aks" and (
        not report.cluster.context or report.cluster.context.startswith("kind-")
    ):
        failures.append("AKS evidence does not identify a non-kind context")
    if report.platform == "aks" and (report.run_id is None or report.paid_session is None):
        failures.append("AKS evidence lacks paid-session provenance")
    if report.platform == "aks" and report.load.mode != "authenticated":
        failures.append("AKS scaling load did not exercise an authenticated API")
    if report.platform == "aks" and report.load.succeeded == 0:
        failures.append("AKS authenticated scaling load completed no protected requests")
    if (
        report.platform == "aks"
        and report.paid_session is not None
        and not report.workload.image.endswith(report.paid_session.image_digest)
    ):
        failures.append("AKS workload image does not match the recorded immutable digest")
    if report.platform == "kind" and (report.run_id is not None or report.paid_session is not None):
        failures.append("local evidence cannot claim a paid session")
    if report.summary.replicas_min_observed != report.hpa.min_replicas:
        failures.append("minimum replicas were not observed")
    if report.summary.replicas_max_observed < report.hpa.max_replicas:
        failures.append("configured maximum replicas were not observed")
    if report.summary.seconds_to_first_scale_up is None:
        failures.append("no scale-up was observed")
    if report.summary.seconds_to_scale_back_to_min is None:
        failures.append("scale-down convergence was not observed")
    durability = report.durability
    if not durability.worker_pod_deleted:
        failures.append("worker deletion was not executed")
    if durability.runs_submitted == 0:
        failures.append("no durable runs were submitted")
    if durability.runs_completed != durability.runs_submitted:
        failures.append("one or more durable runs were lost")
    if durability.runs_failed:
        failures.append("one or more durable runs failed")
    if durability.max_run_attempts < _MIN_RECOVERY_ATTEMPTS:
        failures.append("no in-flight run was recovered under a new fence")
    if failures:
        raise EvidenceError("; ".join(failures))


def load_evidence(content: str) -> HpaEvidenceReport:
    """Parse a JSON report through the strict evidence contract."""
    return HpaEvidenceReport.model_validate(json.loads(content))
