"""Behavioral tests for the deploy identity guard (AGENTS.md Golden Rules 2 and 7)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_deploy_identity.sh"

PERSONAL_REPOSITORY = "Kartik-Hirijaganer/FraudLens"
PERSONAL_EMAIL = "65550498+Kartik-Hirijaganer@users.noreply.github.com"
PERSONAL_ORIGIN = "git@github-personal:Kartik-Hirijaganer/FraudLens.git"
PERSONAL_SUBSCRIPTION = "01417138-33d6-4b26-a0e2-38090780d0ec"
PERSONAL_TENANT = "057e49df-08cb-4abb-a4ee-469fd4f1954e"
WORK_ORIGIN = "git@github.com:khirijaganer-premierhealthgroup/FraudLens.git"


def run_guard(
    *args: str, env_updates: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the guard with a controlled environment and capture its result."""
    env = os.environ.copy()
    for leaked in (
        "GITHUB_REPOSITORY",
        "GITHUB_ACTIONS",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_TENANT_ID",
        "FRAUDLENS_RENDERED_MANIFESTS",
    ):
        env.pop(leaked, None)
    # Azure identity is supplied explicitly so no test depends on a signed-in Azure CLI.
    env.setdefault("AZURE_SUBSCRIPTION_ID", PERSONAL_SUBSCRIPTION)
    env.setdefault("AZURE_TENANT_ID", PERSONAL_TENANT)
    env.update(env_updates or {})
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def personal_repo(tmp_path: Path) -> Path:
    """A throwaway clone that satisfies every local identity expectation."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", PERSONAL_ORIGIN)
    _git(repo, "config", "user.email", PERSONAL_EMAIL)
    return repo


def test_accepts_the_personal_identity(personal_repo: Path) -> None:
    result = run_guard(cwd=personal_repo)
    assert result.returncode == 0
    assert f"deploy identity OK: {PERSONAL_REPOSITORY}" in result.stdout


def test_refuses_a_work_account_remote(personal_repo: Path) -> None:
    _git(personal_repo, "remote", "set-url", "origin", WORK_ORIGIN)

    result = run_guard(cwd=personal_repo)

    assert result.returncode == 1
    assert "khirijaganer-premierhealthgroup/FraudLens is not" in result.stdout
    assert f"FraudLens deploys only as {PERSONAL_REPOSITORY}" in result.stderr


def test_refuses_a_personal_alias_pointing_at_another_repository(personal_repo: Path) -> None:
    _git(personal_repo, "remote", "set-url", "origin", "git@github-personal:someone/Other.git")

    result = run_guard(cwd=personal_repo)

    assert result.returncode == 1
    assert "FAIL  repository: someone/Other" in result.stdout
    assert "FAIL  origin-remote:" in result.stdout


def test_refuses_a_missing_origin_remote(personal_repo: Path) -> None:
    _git(personal_repo, "remote", "remove", "origin")

    result = run_guard(cwd=personal_repo)

    assert result.returncode == 1
    assert "no GITHUB_REPOSITORY and no 'origin' remote" in result.stdout
    assert "FAIL  origin-remote: no 'origin' remote is configured" in result.stdout


def test_refuses_a_work_commit_email(personal_repo: Path) -> None:
    _git(personal_repo, "config", "user.email", "khirijaganer@premierhealthgroup.health")

    result = run_guard(cwd=personal_repo)

    assert result.returncode == 1
    assert "FAIL  commit-email: khirijaganer@premierhealthgroup.health is not" in result.stdout


def test_refuses_an_unset_commit_email_outside_actions(personal_repo: Path) -> None:
    _git(personal_repo, "config", "--unset", "user.email")

    result = run_guard(cwd=personal_repo, env_updates={"GIT_CONFIG_GLOBAL": os.devnull})

    assert result.returncode == 1
    assert "FAIL  commit-email: <unset> is not" in result.stdout


def test_refuses_a_foreign_azure_subscription(personal_repo: Path) -> None:
    result = run_guard(
        cwd=personal_repo, env_updates={"AZURE_SUBSCRIPTION_ID": "00000000-dead-beef-0000-0"}
    )

    assert result.returncode == 1
    assert "FAIL  azure-subscription: 00000000-dead-beef-0000-0 is not" in result.stdout
    assert f"OK    azure-tenant: {PERSONAL_TENANT}" in result.stdout


def test_refuses_a_foreign_azure_tenant(personal_repo: Path) -> None:
    result = run_guard(
        cwd=personal_repo, env_updates={"AZURE_TENANT_ID": "11111111-dead-beef-1111-1"}
    )

    assert result.returncode == 1
    assert "FAIL  azure-tenant: 11111111-dead-beef-1111-1 is not" in result.stdout


def test_refuses_unresolved_placeholders_in_rendered_output(
    personal_repo: Path, tmp_path: Path
) -> None:
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(
        "identityId: replace-infisical-identity-id\n"
        "azureManagedIdentityClientId: replace-azure-managed-identity-client-id\n",
        encoding="utf-8",
    )

    result = run_guard("--rendered", str(rendered), cwd=personal_repo)

    assert result.returncode == 1
    assert "replace-azure-managed-identity-client-id" in result.stdout
    assert "replace-infisical-identity-id" in result.stdout


def test_accepts_fully_substituted_rendered_output(personal_repo: Path, tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text("identityId: 7f3c2a1b\n", encoding="utf-8")

    result = run_guard("--rendered", str(rendered), cwd=personal_repo)

    assert result.returncode == 0
    assert "resolves every placeholder" in result.stdout


def test_reads_rendered_paths_from_the_environment(personal_repo: Path, tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text("identityId: replace-infisical-identity-id\n", encoding="utf-8")

    result = run_guard(
        cwd=personal_repo, env_updates={"FRAUDLENS_RENDERED_MANIFESTS": str(rendered)}
    )

    assert result.returncode == 1
    assert "replace-infisical-identity-id" in result.stdout


def test_refuses_a_rendered_path_that_does_not_exist(personal_repo: Path, tmp_path: Path) -> None:
    result = run_guard("--rendered", str(tmp_path / "absent.yaml"), cwd=personal_repo)

    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_skips_placeholder_scan_when_no_rendered_output_is_supplied(personal_repo: Path) -> None:
    result = run_guard(cwd=personal_repo)

    assert result.returncode == 0
    assert "SKIP  rendered-placeholders" in result.stdout


def test_actions_checkout_uses_the_repository_slug_instead_of_the_ssh_alias(
    personal_repo: Path,
) -> None:
    _git(
        personal_repo,
        "remote",
        "set-url",
        "origin",
        f"https://github.com/{PERSONAL_REPOSITORY}",
    )
    _git(personal_repo, "config", "--unset", "user.email")

    result = run_guard(
        cwd=personal_repo,
        env_updates={
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": PERSONAL_REPOSITORY,
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    )

    assert result.returncode == 0
    assert "SKIP  commit-email" in result.stdout
    assert "SKIP  origin-remote" in result.stdout


def test_actions_checkout_still_refuses_a_foreign_repository(personal_repo: Path) -> None:
    result = run_guard(
        cwd=personal_repo,
        env_updates={
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "khirijaganer-premierhealthgroup/FraudLens",
        },
    )

    assert result.returncode == 1
    assert "source: GITHUB_REPOSITORY" in result.stdout


def test_skips_azure_checks_when_no_identity_source_exists(
    personal_repo: Path, tmp_path: Path
) -> None:
    """`make pre-pr` must not require an Azure login, so an unusable CLI is a skip."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    signed_out_az = stub_dir / "az"
    signed_out_az.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    signed_out_az.chmod(0o755)
    env = {
        "AZURE_SUBSCRIPTION_ID": "",
        "AZURE_TENANT_ID": "",
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
    }

    result = run_guard(cwd=personal_repo, env_updates=env)

    assert result.returncode == 0
    assert "SKIP  azure-account" in result.stdout


def test_rejects_unexpected_arguments() -> None:
    result = run_guard("unexpected")
    assert result.returncode == 2
    assert "unexpected argument: unexpected" in result.stderr
    assert "usage:" in result.stderr


def test_rejects_a_rendered_flag_without_a_path() -> None:
    result = run_guard("--rendered")
    assert result.returncode == 2
    assert "--rendered requires a path" in result.stderr


def test_help_flag_exits_zero() -> None:
    result = run_guard("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
