"""Structural security and separation contracts for rendered Kubernetes manifests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from lib.k8s_demo.kubectl import resolve_tool

REPO_ROOT = Path(__file__).resolve().parents[2]


def _render(overlay: str) -> list[dict[str, Any]]:
    rendered = subprocess.run(
        [resolve_tool("kubectl"), "kustomize", f"deploy/k8s/{overlay}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [document for document in yaml.safe_load_all(rendered) if document]


def _named(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(
        document
        for document in documents
        if document["kind"] == kind and document["metadata"]["name"] == name
    )


def _pod_specs(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        document["spec"]["template"]["spec"]
        for document in documents
        if document["kind"] in {"Deployment", "Job"}
    ]


def test_kind_workloads_are_hardened_and_bounded() -> None:
    documents = _render("overlays/kind") + _render("load")
    for pod in _pod_specs(documents):
        security = pod["securityContext"]
        assert security["runAsNonRoot"] is True
        assert security["runAsUser"] >= 10_000
        assert security["seccompProfile"]["type"] == "RuntimeDefault"
        assert pod["automountServiceAccountToken"] is False
        for container in [*pod.get("initContainers", []), *pod["containers"]]:
            container_security = container["securityContext"]
            assert container_security["allowPrivilegeEscalation"] is False
            assert container_security["readOnlyRootFilesystem"] is True
            assert container_security["runAsNonRoot"] is True
            assert container_security["runAsUser"] >= 10_000
            assert container_security["capabilities"]["drop"] == ["ALL"]
            assert set(container["resources"]) == {"limits", "requests"}
            assert set(container["resources"]["limits"]) == {"cpu", "memory"}
            assert set(container["resources"]["requests"]) == {"cpu", "memory"}


def test_long_running_probes_exceed_cold_start_budget() -> None:
    documents = _render("overlays/kind")
    for deployment in (doc for doc in documents if doc["kind"] == "Deployment"):
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            for name in ("startupProbe", "livenessProbe"):
                probe = container[name]
                assert probe["failureThreshold"] * probe["periodSeconds"] > 75
        if deployment["metadata"]["name"] in {"fraudlens-api", "postgres"}:
            probe = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
            assert probe["failureThreshold"] * probe["periodSeconds"] > 75


def test_worker_supervisor_exposes_a_killable_child_without_weakening_pid_isolation() -> None:
    worker = _named(_render("overlays/kind"), "Deployment", "fraudlens-worker")
    container = worker["spec"]["template"]["spec"]["containers"][0]
    command = " ".join(container["command"])
    arguments = "\n".join(container["args"])
    assert command == "sh -c"
    assert "python -m fraudlens_backend.worker &" in arguments
    assert "/tmp/fraudlens-worker.pid" in arguments
    assert "trap terminate TERM INT" in arguments


def test_hpa_owns_api_replicas_and_has_bounded_behavior() -> None:
    documents = _render("overlays/kind")
    api = _named(documents, "Deployment", "fraudlens-api")
    hpa_document = _named(documents, "HorizontalPodAutoscaler", "fraudlens-api")
    hpa = hpa_document["spec"]
    assert "replicas" not in api["spec"]
    assert hpa_document["apiVersion"] == "autoscaling/v2"
    assert hpa["scaleTargetRef"] == {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "name": "fraudlens-api",
    }
    assert hpa["minReplicas"] >= 1
    assert hpa["maxReplicas"] == 5
    assert hpa["metrics"][0]["resource"]["target"]["averageUtilization"] == 60
    assert hpa["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 60


def test_namespace_service_account_and_network_policy_fail_closed() -> None:
    documents = _render("overlays/kind")
    namespace = _named(documents, "Namespace", "fraudlens")
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    service_account = _named(documents, "ServiceAccount", "fraudlens-runtime")
    assert service_account["automountServiceAccountToken"] is False
    deny = _named(documents, "NetworkPolicy", "default-deny")
    assert deny["spec"]["podSelector"] == {}
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    policies = "\n".join(
        yaml.safe_dump(document) for document in documents if document["kind"] == "NetworkPolicy"
    )
    assert "169.254.169.254/32" in policies
    assert "port: 8000" in policies
    assert "port: 5432" in policies
    egress = _named(documents, "NetworkPolicy", "application-egress")["spec"]["egress"]
    dns_selector = egress[0]["to"][0]["podSelector"]["matchLabels"]
    assert dns_selector == {"k8s-app": "kube-dns"}
    pod_destinations = {
        tuple(rule["to"][0]["podSelector"]["matchLabels"].items())
        for rule in egress
        if "podSelector" in rule["to"][0]
    }
    assert (("app.kubernetes.io/component", "api"),) in pod_destinations


def test_secret_references_are_optional_on_long_running_apps() -> None:
    documents = _render("overlays/kind")
    for name in ("fraudlens-api", "fraudlens-worker"):
        deployment = _named(documents, "Deployment", name)
        env_from = deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"]
        secret_refs = [item["secretRef"] for item in env_from if "secretRef" in item]
        assert {item["name"] for item in secret_refs} == {
            "fraudlens-backend-secrets",
            "fraudlens-llm-secrets",
        }
        assert all(item["optional"] is True for item in secret_refs)


def test_images_are_pinned_and_platform_overlays_are_separated() -> None:
    kind = _render("overlays/kind")
    aks = _render("overlays/aks-demo")
    load = _render("load")
    for document in [*kind, *aks, *load]:
        if document["kind"] not in {"Deployment", "Job"}:
            continue
        pod = document["spec"]["template"]["spec"]
        for container in [*pod.get("initContainers", []), *pod["containers"]]:
            assert not container["image"].endswith(":latest")
    assert _named(kind, "Deployment", "postgres")
    assert not any(document["kind"] == "PersistentVolumeClaim" for document in aks)
    assert not any(document["metadata"]["name"] == "postgres" for document in aks)
    assert {document["kind"] for document in aks if document["kind"] == "InfisicalSecret"} == {
        "InfisicalSecret"
    }
    for deployment in (document for document in aks if document["kind"] == "Deployment"):
        pod_spec = deployment["spec"]["template"]["spec"]
        # The user pool is regular (on-demand) capacity, so neither the Spot toleration nor the
        # Spot node affinity has anything to match; carrying either would steer scheduling for
        # a taint and a label that are never applied.
        assert "tolerations" not in pod_spec
        assert "affinity" not in pod_spec
        assert deployment["metadata"]["annotations"]["secrets.infisical.com/auto-reload"] == "true"


def test_only_the_aks_overlay_publishes_an_external_service() -> None:
    kind_service = _named(_render("overlays/kind"), "Service", "fraudlens-api")
    aks_service = _named(_render("overlays/aks-demo"), "Service", "fraudlens-api")
    assert kind_service["spec"]["type"] == "ClusterIP"
    assert "externalTrafficPolicy" not in kind_service["spec"]
    assert aks_service["spec"]["type"] == "LoadBalancer"
    assert aks_service["spec"]["externalTrafficPolicy"] == "Local"
    probe_annotation = "service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path"
    assert aks_service["metadata"]["annotations"][probe_annotation] == "/healthz"
    assert {port["port"] for port in aks_service["spec"]["ports"]} == {8000}


def test_only_the_aks_overlay_admits_ingress_from_outside_the_cluster() -> None:
    # A LoadBalancer Service with externalTrafficPolicy: Local hands the pod the real client IP,
    # which matches no namespaceSelector — so without an explicit external rule Cilium drops every
    # request and the published address never answers.
    def _api_ingress(overlay: str) -> list[dict[str, Any]]:
        return _named(_render(overlay), "NetworkPolicy", "api-ingress")["spec"]["ingress"]

    def _external_ports(rules: list[dict[str, Any]]) -> set[int]:
        return {
            port["port"]
            for rule in rules
            for source in rule["from"]
            if source.get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
            for port in rule["ports"]
        }

    aks_rules = _api_ingress("overlays/aks-demo")
    assert _external_ports(aks_rules) == {8000}
    # The in-cluster rule survives the append; the worker and load Job still reach the API.
    assert {"namespaceSelector": {}} in [source for rule in aks_rules for source in rule["from"]]
    assert _external_ports(_api_ingress("overlays/kind")) == set()


def test_aks_overlay_runs_the_in_cluster_worker_queue() -> None:
    # The AKS root creates no Container Apps, so a container_apps_jobs backend would enqueue work
    # to a queue that does not exist; the in-cluster worker claims from Postgres instead.
    config = next(
        document
        for document in _render("overlays/aks-demo")
        if document["kind"] == "ConfigMap"
        and document["metadata"]["name"].startswith("fraudlens-backend-config")
    )
    assert config["data"]["FRAUDLENS_QUEUE_BACKEND"] == "local"
    assert config["data"]["FRAUDLENS_RUN_EXECUTION_MODE"] == "worker"


def test_env_files_contain_no_secret_like_keys() -> None:
    forbidden = {"DATABASE_URL", "POSTGRES_PASSWORD", "API_KEY", "TOKEN", "SECRET"}
    non_secret_contract_keys = {
        "FRAUDLENS_INFISICAL_REQUIRED_ENV_KEYS",
        "FRAUDLENS_INFISICAL_SECRETS_DELIVERY",
    }
    for path in (REPO_ROOT / "deploy").rglob("*.env"):
        keys = {
            line.split("=", 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        }
        keys -= non_secret_contract_keys
        assert not any(any(marker in key for marker in forbidden) for key in keys), path
    kind_kustomization = (REPO_ROOT / "deploy/k8s/overlays/kind/kustomization.yaml").read_text(
        encoding="utf-8"
    )
    assert kind_kustomization.count("changeme") == 2
    assert "Synthetic, kind-only credential" in kind_kustomization


def test_backend_image_declares_numeric_runtime_identity() -> None:
    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile
    assert "USER app" in dockerfile
    assert "COPY --chown=app:app alembic /app/alembic" in dockerfile
