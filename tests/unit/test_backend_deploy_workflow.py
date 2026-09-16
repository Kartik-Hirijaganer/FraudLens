"""Contracts for the Phase 4 backend deploy: image pullability, secret injection, and the smoke.

Container Apps has no secret store of its own, so the deploy workflow IS the injection mechanism.
That makes its shape a security boundary rather than a convenience: which values reach the app,
where they come from, when they land relative to the readiness gate, and what may never be written
down. These cases pin exactly that, and pair the workflow against the Terraform that has to leave
its work alone.

Terraform source is asserted rather than a live plan: every Azure root here is deliberately inert
and `plan` needs credentials CI does not hold.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-backend.yml"
GATEWAY_MODULE = REPO_ROOT / "infra" / "terraform" / "modules" / "gateway_app" / "main.tf"

# The Phase 4 allowlist: Container Apps secret name -> the env var the application reads.
INJECTED_SECRETS = {
    "database-url": "DATABASE_URL",
    "supabase-service-role-key": "SUPABASE_SERVICE_ROLE_KEY",
    "demo-auth-password": "FRAUDLENS_DEMO_AUTH_PASSWORD",
    "openrouter-api-key": "OPENROUTER_API_KEY",
}
# Derived at injection time from SUPABASE_URL rather than stored a second time.
DERIVED_ENV = ("FRAUDLENS_AUTH_JWKS_URL", "FRAUDLENS_AUTH_JWT_ISSUER")
# The project keeps its shared service secrets at the ROOT and only the provider key under
# /llm. There is no /backend folder: fetching one fails the deploy at its first step.
INFISICAL_PATHS = ("/llm", "/")
# The subset whose VALUES the post-deploy log scan searches for. The synthetic demo password
# is deliberately excluded: it is public by design, so a hit would be noise, not a leak.
SCANNED_SECRETS = frozenset({"DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY"})
# Azure's own limit on `<app name>--<revision suffix>`.
_AZURE_REVISION_NAME_LIMIT = 54
# `az containerapp logs show` accepts --tail between 0 and 300.
_AZURE_LOG_TAIL_LIMIT = 300


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(_source())
    assert isinstance(loaded, dict)
    return loaded


def _job(name: str) -> dict[str, Any]:
    job = _workflow()["jobs"][name]
    assert isinstance(job, dict)
    return job


def _steps(name: str) -> list[dict[str, Any]]:
    steps = _job(name)["steps"]
    assert isinstance(steps, list)
    return steps


def _step(job: str, needle: str) -> dict[str, Any]:
    """Return the first step in `job` whose name contains `needle`."""
    for step in _steps(job):
        if needle in str(step.get("name", "")):
            return step
    pytest.fail(f"no step matching '{needle}' in job '{job}'")


def _index(job: str, needle: str) -> int:
    for position, step in enumerate(_steps(job)):
        if needle in str(step.get("name", "")):
            return position
    pytest.fail(f"no step matching '{needle}' in job '{job}'")


# --- how a release branch deploys its OWN workflow -------------------------------------------


def test_the_deploy_can_be_dispatched_against_the_branch_being_released() -> None:
    """`workflow_run` runs the DEFAULT branch's copy of this file, whatever ref triggered it.

    That makes every deploy change on a release branch inert until it is merged, which turns one
    fix into one merge to `main`. A dispatch executes the file from the ref it targets, so the
    branch iterates on its own deploy.
    """
    triggers = _workflow()[True] if True in _workflow() else _workflow()["on"]
    assert isinstance(triggers, dict)
    assert "workflow_dispatch" in triggers
    assert "workflow_run" in triggers, "the automatic post-CI deploy must survive"


@pytest.mark.parametrize("job", ["identity", "verify"])
def test_a_dispatch_reaches_the_same_gates_as_a_post_ci_deploy(job: str) -> None:
    condition = _job(job)["if"]
    assert "github.event_name == 'workflow_dispatch'" in condition
    # The post-CI path keeps its original guard: a red or non-push CI run still deploys nothing.
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.event == 'push'" in condition


def test_a_dispatch_deploys_the_ref_it_was_dispatched_against() -> None:
    # On a dispatch `github.event.workflow_run` is absent, so every SHA reference has to fall
    # back to the dispatched ref or the deploy would build and stage an empty tag.
    body = _source()
    assert "${{ github.event.workflow_run.head_sha }}" not in body
    assert body.count("${{ github.event.workflow_run.head_sha || github.sha }}") >= 1
    assert "DEPLOY_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in body


def test_dispatching_does_not_weaken_any_gate() -> None:
    # A manual trigger must not become a way around CI parity, the feature flag, or approval.
    jobs = _workflow()["jobs"]
    assert jobs["verify"]["needs"] == "identity"
    assert jobs["build-push"]["needs"] == "verify"
    for name in ("build-push", "infra", "stage", "migrate", "smoke", "promote"):
        assert jobs[name]["environment"] == "production", name
        if "if" in jobs[name]:
            assert "AZURE_DEPLOY_ENABLED" in jobs[name]["if"], name


def test_the_revision_name_fits_the_limit_azure_actually_enforces() -> None:
    """Azure caps the revision name at 54 characters COMBINED with the container app name.

    The full commit SHA is 40 characters and the app name spends 20 with its separator, so a
    suffix built from the whole SHA is rejected outright -- `ContainerAppInvalidRevisionName`,
    after the secrets have already been injected and the deploy is most of the way through.
    """
    app_name = _workflow()["env"]["APP_NAME"]
    meta = _step("build-push", "Compute image reference")["run"]
    assert "revision_suffix=s${DEPLOY_SHA:0:12}-${GITHUB_RUN_NUMBER}-${GITHUB_RUN_ATTEMPT}" in meta
    # Longest plausible rendered name: app + "--" + "s" + 12 SHA chars + a 7-digit run number
    # and a 2-digit attempt, each with its separator.
    assert len(f"{app_name}--s{'0' * 12}-{'0' * 7}-{'0' * 2}") <= _AZURE_REVISION_NAME_LIMIT
    # The image tag keeps the FULL sha: that is the build-once identity.
    assert 'echo "image=${image_name}:${DEPLOY_SHA}"' in meta


def test_the_revision_name_is_unique_per_deploy_not_merely_per_commit() -> None:
    # A revision name is taken for good once used -- deactivating one does not release it. A
    # SHA-only suffix therefore fails every redeploy of the same commit, which is exactly what
    # bringing a release up looks like.
    meta = _step("build-push", "Compute image reference")["run"]
    assert "${GITHUB_RUN_NUMBER}" in meta
    assert "${GITHUB_RUN_ATTEMPT}" in meta


def test_every_job_that_names_the_revision_reads_one_computed_suffix() -> None:
    # Four jobs name the revision. Deriving the truncation in each would be four places for the
    # limit to drift; they all read the value build-push computed.
    jobs = _workflow()["jobs"]
    for name in ("stage", "smoke", "promote", "abort"):
        assert jobs[name]["env"]["REVISION_SUFFIX"] == (
            "${{ needs.build-push.outputs.revision_suffix }}"
        ), name
        assert "build-push" in jobs[name]["needs"], name
    assert "REVISION_SUFFIX" not in _workflow()["env"]


# --- the image the Container App will pull anonymously --------------------------------------


def test_the_deploy_proves_the_image_is_pullable_without_a_credential() -> None:
    # acr_enabled = false: the app pulls with no credential, so a private package fails inside
    # Azure as an opaque image-pull error against a revision that already exists.
    step = _step("build-push", "anonymously pullable")
    assert "scripts/check_public_image.py" in step["run"]
    assert step["env"]["IMAGE"] == "${{ steps.meta.outputs.image }}"
    assert _index("build-push", "anonymously pullable") > _index("build-push", "Build & push")


def test_a_failed_pullability_check_is_not_routed_around_by_a_private_registry() -> None:
    # The remediation must stay "publish the package". Wiring a pull credential instead would
    # restore the deploy while quietly adding a registry the cost model does not price.
    body = _source()
    login = _step("build-push", "Log in to GHCR")
    assert login["with"]["registry"] == "ghcr.io"  # a PUSH credential, never a pull one
    for private_registry in ("azurecr.io", "--registry-server", "az acr login"):
        assert private_registry not in body, private_registry


# --- secret injection: what reaches the app, from where, and when ----------------------------


@pytest.mark.parametrize("job", ["stage", "smoke"])
def test_every_injected_secret_comes_from_infisical_over_oidc(job: str) -> None:
    fetches = [step for step in _steps(job) if "Infisical" in str(step.get("uses", ""))]
    assert [step["with"]["secret-path"] for step in fetches] == list(INFISICAL_PATHS)
    for step in fetches:
        assert step["with"]["method"] == "oidc"
        assert step["with"]["env-slug"] == "prod"
        assert "token" not in step["with"], "a stored Infisical token would defeat the OIDC posture"


def test_exactly_the_four_allowlisted_secrets_are_injected() -> None:
    # The allowlist IS the contract. A fifth value would widen the app's blast radius silently.
    script = _step("stage", "four allowlisted runtime secrets")["run"]
    allowed = [
        line.split()[1:] for line in script.splitlines() if line.strip().startswith("allow ")
    ]
    assert [tuple(pair) for pair in allowed] == list(INJECTED_SECRETS.items())
    assert "az containerapp secret set" in script


def test_a_missing_secret_fails_the_deploy_naming_only_the_variable() -> None:
    script = _step("stage", "four allowlisted runtime secrets")["run"]
    assert "missing" in script
    assert "names only — never a value" in script
    assert "exit 1" in script
    # SUPABASE_URL is not injected as a secret, but the derived JWKS/issuer are useless without it.
    assert 'missing+=("SUPABASE_URL")' in script


def test_the_staged_revision_itself_carries_the_secret_references() -> None:
    # Env vars are revision-scoped. Setting them in a LATER call would create a second revision
    # and leave the one smoke tests without them — /readyz would then pass on the wrong process.
    script = _step("stage", "Create revision at 0% traffic")["run"]
    assert "--revision-suffix" in script and "--image" in script
    for secret_name, env_name in INJECTED_SECRETS.items():
        assert f'"{env_name}=secretref:{secret_name}"' in script
    for derived in DERIVED_ENV:
        assert f'"{derived}=' in script
    assert "${SUPABASE_URL%/}" in script


def test_the_deployed_app_is_told_whether_it_carries_a_demo_story() -> None:
    """Bootstrapping the story does not expose it: the route fails closed on the app's own flag.

    Tied to the same variable that gates the bootstrap, so the data and the endpoint cannot
    disagree — a deployment with no story must not advertise a demo route, and one that has the
    story must serve it. Defaulted in the shell, because an unset repo variable renders as the
    empty string and pydantic-settings rejects that as a boolean.
    """
    step = _step("stage", "Create revision at 0% traffic")
    assert step["env"]["DEMO_ENABLED"] == "${{ vars.PORTFOLIO_DEMO_BOOTSTRAP_ENABLED }}"
    assert '"FRAUDLENS_PORTFOLIO_DEMO_ENABLED=${DEMO_ENABLED:-false}"' in step["run"]


def test_the_jwks_url_and_issuer_are_derived_rather_than_stored_twice() -> None:
    script = _step("stage", "Create revision at 0% traffic")["run"]
    assert '"FRAUDLENS_AUTH_JWKS_URL=${supabase_url}/auth/v1/.well-known/jwks.json"' in script
    assert '"FRAUDLENS_AUTH_JWT_ISSUER=${supabase_url}/auth/v1"' in script


def test_injection_happens_before_the_readiness_gate_can_observe_it() -> None:
    jobs = _workflow()["jobs"]
    assert "stage" in jobs["smoke"]["needs"]
    assert _index("stage", "four allowlisted runtime secrets") < _index(
        "stage", "Create revision at 0% traffic"
    )


def test_no_secret_value_is_ever_written_into_the_workflow() -> None:
    # Values are read out of the job environment by NAME. The only `secrets.` reference in the
    # file is the ephemeral GITHUB_TOKEN used to PUSH the image.
    body = _source()
    assert body.count("${{ secrets.") == 1
    assert "${{ secrets.GITHUB_TOKEN }}" in body
    for env_name in INJECTED_SECRETS.values():
        assert f"${{{{ vars.{env_name} }}}}" not in body


def test_terraform_never_owns_the_injected_secret_values() -> None:
    # Golden Rule 3: a secret that reached terraform would be written to remote state in clear.
    gateway = GATEWAY_MODULE.read_text(encoding="utf-8")
    for secret_name, env_name in INJECTED_SECRETS.items():
        assert secret_name not in gateway, secret_name
        assert f'name  = "{env_name}"' not in gateway, env_name


def test_a_later_terraform_apply_cannot_strip_the_injected_secrets() -> None:
    # D7: without `secret` in ignore_changes the next apply reconciles the app back to a resource
    # that declares no secrets at all, and every secretref env var resolves to nothing.
    gateway = GATEWAY_MODULE.read_text(encoding="utf-8")
    ignored = gateway.split("ignore_changes = [")[1].split("\n  ]")[0]
    assert "secret," in ignored


def test_the_apply_supplies_every_variable_the_prod_root_requires() -> None:
    """A required variable with no default stops `plan` dead, before anything is created.

    The budget recipient and start month are human-owned (Golden Rule 3), so the prod root
    declares them without defaults. They reach terraform only through TF_VAR_*, which means the
    workflow is the single place that can supply them — and the only place this can be missed.
    """
    root = REPO_ROOT / "infra" / "terraform" / "environments" / "prod" / "variables.tf"
    source = root.read_text(encoding="utf-8")
    required = {
        name
        for name in re.findall(r'variable "([a-z_]+)"', source)
        if "default" not in source.split(f'variable "{name}"')[1].split("\n}")[0]
    }
    supplied = set(_step("infra", "Terraform plan")["env"])
    tfvars = (
        REPO_ROOT / "infra" / "terraform" / "environments" / "prod" / "prod.tfvars"
    ).read_text(encoding="utf-8")
    for name in sorted(required):
        assigned_in_tfvars = re.search(rf"^\s*{name}\s*=", tfvars, re.MULTILINE) is not None
        # `container_image` is stamped as a -var on the plan command line, not via TF_VAR_*.
        assert assigned_in_tfvars or f"TF_VAR_{name}" in supplied or name == "container_image", (
            f"{name} has no default, is not in prod.tfvars, and is not passed as TF_VAR_{name}"
        )


def test_the_budget_recipient_reaches_terraform_as_a_list_not_a_bare_string() -> None:
    # `budget_contact_emails` is list(string); a bare address fails type validation at plan time.
    env = _step("infra", "Terraform plan")["env"]
    assert env["TF_VAR_budget_contact_emails"] == '["${{ vars.AZURE_BUDGET_CONTACT_EMAIL }}"]'
    assert env["TF_VAR_budget_start_date"] == "${{ vars.AZURE_BUDGET_START_DATE }}"


def test_the_apply_decision_cannot_be_silently_rewritten_by_a_wrapper() -> None:
    """Skipping the apply is the one outcome that looks like success while doing nothing.

    `hashicorp/setup-terraform` wraps the binary by default and does not preserve
    `-detailed-exitcode`, so terraform's "2 = changes pending" arrived as 0 and a plan with 14
    resources to add was skipped with the job green. The wrapper is disabled, and the decision is
    additionally cross-checked against the plan file so no single status can strand the deploy.
    """
    setup = next(step for step in _steps("infra") if "setup-terraform" in str(step.get("uses", "")))
    assert setup["with"]["terraform_wrapper"] is False
    script = _step("infra", "Terraform plan")["run"]
    assert "terraform show -json tfplan" in script
    assert 'if [ "$code" = "2" ] || [ "$changes" != "0" ]; then' in script


# --- the authenticated smoke ----------------------------------------------------------------


def test_the_smoke_runs_under_the_environment_its_identities_are_federated_to() -> None:
    # Both the Infisical machine identity and the Azure federated subject are registered against
    # `environment:production`; without it the smoke's OIDC exchange fails at runtime.
    assert _job("smoke")["environment"] == "production"


def test_the_smoke_signs_in_as_a_permitted_and_a_refused_persona() -> None:
    script = _step("smoke", "Mint short-lived persona tokens")["run"]
    assert "scripts/smoke_auth_token.py" in script
    assert "--export analyst=SMOKE_AUTH_TOKEN" in script
    assert "--export auditor=SMOKE_AUDITOR_TOKEN" in script
    assert _index("smoke", "Mint short-lived persona tokens") < _index("smoke", "Smoke the staged")


def test_the_smoke_targets_the_staged_revision_not_the_promoted_one() -> None:
    step = _step("smoke", "Smoke the staged")
    assert step["env"]["SMOKE_BASE_URL"] == "${{ needs.stage.outputs.revision_fqdn }}"
    assert "pytest tests/smoke -m smoke" in step["run"]


def test_the_smoke_collects_only_the_directory_its_selection_lives_in() -> None:
    """`-m smoke` deselects, it does not stop pytest IMPORTING what it collects.

    Collecting the whole tree pulls in modules that need the optional `fulldata` group, which the
    deploy does not install, and collection aborts on ModuleNotFoundError before one smoke test
    runs. Scoping to the directory is safe precisely because every smoke-marked test lives there.
    """
    smoke_dir = REPO_ROOT / "tests" / "smoke"
    # Matched on real marker syntax, not the bare substring: this module discusses the marker in
    # prose and would otherwise detect itself.
    markers = ("pytestmark = pytest.mark.smoke", "@pytest.mark.smoke")
    this_module = Path(__file__).name
    marked = {
        path.name
        for path in (REPO_ROOT / "tests").rglob("test_*.py")
        if path.name != this_module
        and any(marker in path.read_text(encoding="utf-8") for marker in markers)
    }
    assert marked, "no smoke-marked module found at all"
    assert marked == {path.name for path in smoke_dir.glob("test_*.py")}


def test_the_smoke_refuses_secret_jwt_phi_or_stack_trace_leakage_in_the_logs() -> None:
    step = _step("smoke", "leakage in the revision log")
    assert "az containerapp logs show" in step["run"]
    assert "scripts/check_log_leakage.py" in step["run"]
    # `az containerapp logs show` rejects --tail above 300 outright, so an over-large value fails
    # the gate on its arguments rather than on anything it found.
    tail = int(re.search(r"--tail (\d+)", str(step["run"])).group(1))
    assert 0 < tail <= _AZURE_LOG_TAIL_LIMIT
    for env_name in SCANNED_SECRETS:
        assert f"--secret-env {env_name}" in step["run"]
    assert _index("smoke", "leakage in the revision log") > _index("smoke", "Smoke the staged")


def test_the_smoke_holds_every_secret_value_the_leak_scan_searches_for() -> None:
    # A name with nothing behind it makes the scan pass over any log at all, so the job must
    # fetch the same Infisical paths the injection reads.
    fetched = {
        str(step["with"]["secret-path"])
        for step in _steps("smoke")
        if "Infisical" in str(step.get("uses", ""))
    }
    assert fetched == set(INFISICAL_PATHS)
    # A path the project does not have fails the whole deploy at its first fetch. Asserted on the
    # fetch declarations, not the file text: the comments deliberately name /backend to record
    # why it is absent.
    declared = {
        str(step["with"]["secret-path"])
        for job in ("stage", "migrate", "smoke")
        for step in _steps(job)
        if "Infisical" in str(step.get("uses", ""))
    }
    assert "/backend" not in declared
    # Same absence across every workflow: `/backend` is not a path this project has.
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    for path in workflows:
        assert "secret-path: /backend" not in path.read_text(encoding="utf-8"), path.name
    assert set(INJECTED_SECRETS.values()) >= SCANNED_SECRETS


# --- the demo identities the smoke depends on ------------------------------------------------


def test_the_personas_are_provisioned_before_the_story_is_bootstrapped() -> None:
    # The story records audited actions under the personas and the smoke signs in as two of them,
    # so the Supabase identities and their `public.users` rows must exist before either.
    gate = "${{ vars.PORTFOLIO_DEMO_BOOTSTRAP_ENABLED == 'true' }}"
    provision = _step("migrate", "Provision the synthetic demo personas")
    bootstrap = _step("migrate", "Rebuild the portfolio demo story")
    assert provision["if"] == gate and bootstrap["if"] == gate
    assert "scripts/provision_demo_auth.py" in provision["run"]
    assert _index("migrate", "Provision the synthetic demo personas") < _index(
        "migrate", "Rebuild the portfolio demo story"
    )


def test_the_deploy_rebuilds_the_story_rather_than_resuming_a_drifted_one() -> None:
    """A deploy must land on the configured distribution, not refuse because the demo was used.

    The smoke starts a real investigation and visitors resolve alerts, so the tenant stops
    matching `expected` almost immediately. A plain bootstrap refuses on that drift, which would
    fail every second deploy until someone reset by hand.
    """
    step = _step("migrate", "Rebuild the portfolio demo story")
    assert "scripts/bootstrap_portfolio_demo.py --reset" in step["run"]
    assert step["env"]["FRAUDLENS_PORTFOLIO_DEMO_ENABLED"] == "true"


# --- the URL the operator is told to publish --------------------------------------------------


def test_promotion_reports_the_stable_ingress_fqdn_not_a_revision_fqdn() -> None:
    # D1: a revision FQDN pasted into AZURE_API_ORIGIN breaks the public URL on the next deploy.
    script = _step("promote", "stable ingress FQDN")["run"]
    assert "properties.configuration.ingress.fqdn" in script
    assert "revision" not in script
