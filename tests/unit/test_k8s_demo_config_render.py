"""Behavioral tests for Kubernetes demo configuration and disposable rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import HttpUrl

from lib.k8s_demo.config import (
    K8sDemoConfigError,
    LoadConfig,
    load_config,
    load_config_from_env,
)
from lib.k8s_demo.kubectl import CommandResult
from lib.k8s_demo.render import (
    _render_tree,
    _split_image,
    deploy,
    render_load_job,
    render_overlay,
)


def test_committed_config_is_strict_and_pinned() -> None:
    config = load_config()
    assert config.context == "kind-fraudlens-demo"
    assert "@sha256:" in config.kind_node_image
    assert config.load.mode == "healthz"


def test_config_rejects_missing_and_extra_fields(tmp_path: Path) -> None:
    with pytest.raises(K8sDemoConfigError, match="missing"):
        load_config(tmp_path / "absent.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("cluster_name: demo\nunexpected: true\n", encoding="utf-8")
    with pytest.raises(K8sDemoConfigError, match="invalid"):
        load_config(invalid)


def test_load_environment_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "LOAD_TARGET_URL": "http://service:8000/healthz",
        "LOAD_MODE": "healthz",
        "LOAD_CONCURRENCY": "4",
        "LOAD_DURATION_SECONDS": "5",
        "LOAD_RECONNECT_EVERY": "10",
        "LOAD_CASES": "2",
        "LOAD_AUTH_REQUIRED": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert load_config_from_env().concurrency == 4
    monkeypatch.setenv("LOAD_MODE", "invalid")
    with pytest.raises(K8sDemoConfigError, match="environment is invalid"):
        load_config_from_env()


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("repo/app:sha", ("repo/app", "sha", None)),
        ("registry:5000/repo/app:tag", ("registry:5000/repo/app", "tag", None)),
        ("repo/app@sha256:abc", ("repo/app", None, "sha256:abc")),
        ("repo/app", ("repo/app", None, None)),
    ],
)
def test_image_split_handles_tags_digests_and_registry_ports(
    reference: str, expected: tuple[str, str | None, str | None]
) -> None:
    assert _split_image(reference) == expected


def test_overlay_render_is_disposable_and_overrides_every_backend_image() -> None:
    config = load_config()
    source = Path("deploy/k8s/overlays/kind/kustomization.yaml").read_text(encoding="utf-8")
    rendered = render_overlay(config, "kind", image="example.invalid/fraudlens:measured")
    assert rendered.image == "example.invalid/fraudlens:measured"
    assert "example.invalid/fraudlens:measured" in rendered.yaml_text
    assert "fraudlens-backend:local" not in rendered.yaml_text
    assert Path("deploy/k8s/overlays/kind/kustomization.yaml").read_text(encoding="utf-8") == source


def test_aks_render_injects_every_operator_value_or_refuses() -> None:
    config = load_config()
    rendered = render_overlay(
        config,
        "aks",
        image="example.invalid/fraudlens:sha",
        infisical_identity_id="identity-123",
        azure_managed_identity_client_id="client-456",
        infisical_project_slug="fraudlens",
    ).yaml_text
    assert rendered.count("identityId: identity-123") == 2
    assert rendered.count("azureManagedIdentityClientId: client-456") == 2
    assert rendered.count("projectSlug: fraudlens") == 2
    assert "replace-" not in rendered
    with pytest.raises(ValueError, match="requires every"):
        render_overlay(config, "aks", infisical_identity_id="identity-123")
    with pytest.raises(ValueError, match="unsupported"):
        render_overlay(
            config,
            "aks",
            infisical_identity_id="identity value",
            azure_managed_identity_client_id="client-456",
            infisical_project_slug="fraudlens",
        )
    with pytest.raises(ValueError, match="aks platform"):
        render_overlay(
            config,
            "kind",
            infisical_identity_id="identity-123",
            azure_managed_identity_client_id="client-456",
            infisical_project_slug="fraudlens",
        )


def test_aks_render_refuses_a_manifest_with_a_surviving_placeholder() -> None:
    # A new operator placeholder that render.py does not know how to substitute must abort the
    # render rather than reach a cluster as the literal string "replace-...".
    config = load_config()
    original = _render_tree

    def leaky_render(*args: object, **kwargs: object) -> str:
        return original(*args, **kwargs) + "\n# replace-unmapped-operator-input\n"  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("lib.k8s_demo.render._render_tree", leaky_render)
        with pytest.raises(ValueError, match="1 unresolved placeholder"):
            render_overlay(
                config,
                "aks",
                infisical_identity_id="identity-123",
                azure_managed_identity_client_id="client-456",
                infisical_project_slug="fraudlens",
            )


def test_load_render_uses_validated_overrides() -> None:
    config = load_config()
    load = LoadConfig(
        target_url=HttpUrl("http://fraudlens-api:8000"),
        mode="investigations",
        concurrency=7,
        duration_seconds=12,
        reconnect_every=9,
        cases=3,
        auth_required=True,
    )
    rendered = render_load_job(config, load, image="fraudlens-backend:proof")
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    config_map = next(document for document in documents if document["kind"] == "ConfigMap")
    job = next(document for document in documents if document["kind"] == "Job")
    assert config_map["data"]["LOAD_MODE"] == "investigations"
    assert config_map["data"]["LOAD_CONCURRENCY"] == "7"
    assert config_map["data"]["LOAD_AUTH_REQUIRED"] == "true"
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == "fraudlens-backend:proof"
    token_ref = job["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"]
    assert token_ref["secretKeyRef"] == {
        "name": "fraudlens-load-auth",
        "key": "token",
        "optional": True,
    }


def test_deploy_applies_kind_in_bootstrap_order() -> None:
    config = load_config()
    calls: list[list[str]] = []
    inputs: list[str | None] = []

    def runner(
        command: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        del check, timeout
        calls.append(command)
        inputs.append(input_text)
        stdout = f"{config.context}\n" if "current-context" in command else ""
        if "kustomize" in command:
            stdout = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: fraudlens\n"
        return CommandResult(returncode=0, stdout=stdout, stderr="")

    deploy(config, "kind", runner=runner)
    flattened = [" ".join(call) for call in calls]
    assert any("delete job fraudlens-bootstrap" in call for call in flattened)
    assert any("wait deployment/postgres" in call for call in flattened)
    assert any("wait job/fraudlens-bootstrap" in call for call in flattened)
    assert any(input_text and "kind: Namespace" in input_text for input_text in inputs)


def test_confirmed_aks_deploy_has_no_kind_waits() -> None:
    config = load_config()
    calls: list[list[str]] = []

    def runner(
        command: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        del input_text, check, timeout
        calls.append(command)
        if "current-context" in command:
            return CommandResult(returncode=0, stdout="aks-demo\n", stderr="")
        if "kustomize" in command:
            return CommandResult(
                returncode=0,
                stdout="apiVersion: v1\nkind: Namespace\nmetadata:\n  name: fraudlens\n",
                stderr="",
            )
        return CommandResult(returncode=0, stdout="", stderr="")

    deploy(
        config,
        "aks",
        image="app:sha",
        infisical_identity_id="identity-123",
        azure_managed_identity_client_id="client-456",
        infisical_project_slug="fraudlens",
        confirmed=True,
        runner=runner,
    )
    flattened = [" ".join(call) for call in calls]
    assert any("rollout status deployment/fraudlens-api" in call for call in flattened)
    assert any("rollout status deployment/fraudlens-worker" in call for call in flattened)
