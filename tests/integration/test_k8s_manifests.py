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
        tolerations = deployment["spec"]["template"]["spec"]["tolerations"]
        assert any(
            item["value"] == "spot" and item["effect"] == "NoSchedule" for item in tolerations
        )
        assert deployment["metadata"]["annotations"]["secrets.infisical.com/auto-reload"] == "true"


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
