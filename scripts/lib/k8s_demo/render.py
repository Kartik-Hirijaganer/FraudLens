"""Summary: Deterministic Kustomize rendering and guarded workload deployment for FraudLens.

Key classes:
- RenderedManifest: platform, image, and rendered YAML returned across the CLI boundary.

Key functions:
- render_overlay: render kind or AKS manifests from a disposable Kustomize tree.
- render_load_job: render a parameterized in-cluster load Job without editing tracked files.
- deploy: apply an overlay and wait for the ordered kind bootstrap/rollout contract.

Notes:
- Image and load overrides are written only inside TemporaryDirectory; source manifests stay clean.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from lib.k8s_demo.config import REPO_ROOT, K8sDemoConfig, LoadConfig
from lib.k8s_demo.kubectl import CommandRunner, Kubectl, run_command

Platform = Literal["kind", "aks"]
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class RenderedManifest(BaseModel):
    """A rendered manifest and the parameters that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: Platform = Field(..., description="Target overlay.")
    image: str = Field(..., description="Resolved backend image reference.")
    yaml_text: str = Field(..., min_length=1, description="Rendered multi-document YAML.")


def _split_image(reference: str) -> tuple[str, str | None, str | None]:
    """Split a container reference into name, tag, and digest without confusing registry ports."""
    if "@" in reference:
        name, digest = reference.rsplit("@", 1)
        return name, None, digest
    tail = reference.rsplit("/", 1)[-1]
    if ":" in tail:
        name, tag = reference.rsplit(":", 1)
        return name, tag, None
    return reference, None, None


def _set_image(kustomization: Path, image: str) -> None:
    """Replace the fraudlens-backend images entry inside a disposable Kustomization."""
    document = yaml.safe_load(kustomization.read_text(encoding="utf-8"))
    name, tag, digest = _split_image(image)
    override: dict[str, str] = {"name": "fraudlens-backend", "newName": name}
    if tag:
        override["newTag"] = tag
    if digest:
        override["digest"] = digest
    document["images"] = [override]
    kustomization.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _render_tree(
    relative_path: Path,
    *,
    image: str,
    runner: CommandRunner,
    kubectl_binary: str,
) -> str:
    """Copy deploy/k8s, set the image, and run the bundled kubectl Kustomize renderer."""
    with tempfile.TemporaryDirectory(prefix="fraudlens-k8s-render-") as directory:
        root = Path(directory) / "k8s"
        shutil.copytree(REPO_ROOT / "deploy" / "k8s", root)
        target = root / relative_path
        _set_image(target / "kustomization.yaml", image)
        result = runner([kubectl_binary, "kustomize", str(target)])
    return result.stdout


def render_overlay(  # noqa: PLR0913 - explicit render inputs keep the command boundary injectable.
    config: K8sDemoConfig,
    platform: Platform,
    *,
    image: str | None = None,
    infisical_identity_id: str | None = None,
    azure_managed_identity_client_id: str | None = None,
    runner: CommandRunner = run_command,
    kubectl_binary: str | None = None,
) -> RenderedManifest:
    """Render the chosen platform overlay with an explicit immutable image reference."""
    kubectl = Kubectl(config, runner=runner, binary=kubectl_binary)
    selected_image = image or config.image
    rendered = _render_tree(
        Path("overlays") / ("aks-demo" if platform == "aks" else "kind"),
        image=selected_image,
        runner=runner,
        kubectl_binary=kubectl.binary,
    )
    identities = (infisical_identity_id, azure_managed_identity_client_id)
    if any(identities):
        if platform != "aks" or not all(identities):
            raise ValueError("AKS operator rendering requires both managed-identity values")
        if not all(_IDENTITY_PATTERN.fullmatch(value or "") for value in identities):
            raise ValueError("AKS operator identity values contain unsupported characters")
        rendered = rendered.replace("replace-infisical-identity-id", infisical_identity_id or "")
        rendered = rendered.replace(
            "replace-azure-managed-identity-client-id",
            azure_managed_identity_client_id or "",
        )
    return RenderedManifest(platform=platform, image=selected_image, yaml_text=rendered)


def render_load_job(
    config: K8sDemoConfig,
    load: LoadConfig,
    *,
    image: str | None = None,
    runner: CommandRunner = run_command,
    kubectl_binary: str | None = None,
) -> str:
    """Render a disposable load Job with the validated runtime parameters."""
    kubectl = Kubectl(config, runner=runner, binary=kubectl_binary)
    with tempfile.TemporaryDirectory(prefix="fraudlens-k8s-load-") as directory:
        root = Path(directory) / "k8s"
        shutil.copytree(REPO_ROOT / "deploy" / "k8s", root)
        target = root / "load"
        _set_image(target / "kustomization.yaml", image or config.image)
        env_path = target / "load.env"
        env_path.write_text(
            "\n".join(
                [
                    f"LOAD_MODE={load.mode}",
                    f"LOAD_TARGET_URL={load.target_url}",
                    f"LOAD_CONCURRENCY={load.concurrency}",
                    f"LOAD_DURATION_SECONDS={load.duration_seconds}",
                    f"LOAD_RECONNECT_EVERY={load.reconnect_every}",
                    f"LOAD_CASES={load.cases}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = runner([kubectl.binary, "kustomize", str(target)])
    return result.stdout


def deploy(  # noqa: PLR0913 - explicit deploy inputs keep cloud identity replacement auditable.
    config: K8sDemoConfig,
    platform: Platform,
    *,
    image: str | None = None,
    infisical_identity_id: str | None = None,
    azure_managed_identity_client_id: str | None = None,
    confirmed: bool = False,
    runner: CommandRunner = run_command,
) -> None:
    """Apply an overlay; kind additionally waits for DB bootstrap, API, and worker readiness."""
    kubectl = Kubectl(config, runner=runner)
    manifest = render_overlay(
        config,
        platform,
        image=image,
        infisical_identity_id=infisical_identity_id,
        azure_managed_identity_client_id=azure_managed_identity_client_id,
        runner=runner,
        kubectl_binary=kubectl.binary,
    )
    if platform == "kind":
        kubectl.assert_mutation_allowed(platform="kind")
        kubectl.run(
            [
                "-n",
                config.namespace,
                "delete",
                "job",
                "fraudlens-bootstrap",
                "--ignore-not-found=true",
            ]
        )
    kubectl.apply(manifest.yaml_text, platform=platform, confirmed=confirmed)
    if platform == "kind":
        timeout = config.startup_timeout_seconds
        kubectl.wait_for("deployment/postgres", "Available", timeout)
        kubectl.wait_for("job/fraudlens-bootstrap", "Complete", timeout)
        kubectl.wait_for("deployment/fraudlens-api", "Available", timeout)
        kubectl.wait_for("deployment/fraudlens-worker", "Available", timeout)
    else:
        for deployment_name in ("fraudlens-api", "fraudlens-worker"):
            kubectl.run(
                [
                    "-n",
                    config.namespace,
                    "rollout",
                    "status",
                    f"deployment/{deployment_name}",
                    f"--timeout={config.startup_timeout_seconds}s",
                ],
                timeout=config.startup_timeout_seconds + 10,
            )
