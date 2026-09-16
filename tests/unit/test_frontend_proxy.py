"""Contracts for the same-origin Vercel proxy that fronts the Azure gateway.

The browser must never learn a second public hostname: the SPA issues relative `/api/...` requests
and Vercel rewrites them to the gateway, so CORS is not load-bearing and the recruiter-facing URL
never changes when the Container App is recreated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VERCEL_CONFIG = REPO_ROOT / "frontend" / "vercel.json"
PRODUCTION_ENV = REPO_ROOT / "frontend" / ".env.production"
FRONTEND_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-frontend.yml"
_ORIGIN_PLACEHOLDER = "${AZURE_API_ORIGIN}"


def _config() -> dict[str, object]:
    return json.loads(VERCEL_CONFIG.read_text(encoding="utf-8"))


def _rewrites() -> list[dict[str, str]]:
    rewrites = _config()["rewrites"]
    assert isinstance(rewrites, list)
    return rewrites


def test_the_api_rewrite_precedes_the_spa_fallback() -> None:
    # Vercel applies rewrites in order. Behind the catch-all, every /api request — including the
    # SSE event stream — would be answered with index.html instead of reaching the gateway.
    sources = [rewrite["source"] for rewrite in _rewrites()]
    assert sources.index("/api/:path*") < sources.index("/(.*)")


def test_the_api_rewrite_forwards_the_whole_path_to_the_configured_origin() -> None:
    api_rewrite = next(rewrite for rewrite in _rewrites() if rewrite["source"] == "/api/:path*")
    assert api_rewrite["destination"] == f"{_ORIGIN_PLACEHOLDER}/api/:path*"
    # A rewrite proxies server-side; a redirect would bounce the browser to the gateway origin and
    # drop the Authorization header, so no /api redirect may exist.
    assert "redirects" not in _config()


def test_spa_routes_fall_back_to_the_application_shell() -> None:
    fallback = next(rewrite for rewrite in _rewrites() if rewrite["source"] == "/(.*)")
    assert fallback["destination"] == "/index.html"


def test_proxied_api_responses_are_never_cached() -> None:
    # Responses here are tenant-scoped and authenticated; a shared edge cache would serve one
    # agency's data to another.
    headers = _config()["headers"]
    assert isinstance(headers, list)
    api_headers = next(entry for entry in headers if entry["source"] == "/api/(.*)")
    assert {"key": "Cache-Control", "value": "no-store"} in api_headers["headers"]


def test_the_production_build_emits_relative_api_paths() -> None:
    lines = [
        line
        for line in PRODUCTION_ENV.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert lines == ["VITE_API_BASE_URL="]


def test_the_deploy_job_resolves_the_origin_before_building() -> None:
    # Vercel performs no substitution of its own in vercel.json, so an unresolved placeholder
    # would ship as a literal destination and every API call would 404.
    workflow = yaml.safe_load(FRONTEND_WORKFLOW.read_text(encoding="utf-8"))
    deploy = workflow["jobs"]["deploy"]
    build = next(step for step in deploy["steps"] if step.get("name") == "Deploy to Vercel (prod)")
    assert build["env"]["AZURE_API_ORIGIN"] == "${{ vars.AZURE_API_ORIGIN }}"
    script = build["run"]
    assert "AZURE_API_ORIGIN must be an https:// URL" in script
    assert _ORIGIN_PLACEHOLDER in script
    assert script.index(_ORIGIN_PLACEHOLDER) < script.index("vercel build")
    assert "unresolved origin placeholder" in script
    # The SPA no longer receives an absolute API base; that would undo the same-origin proxy.
    assert "VITE_API_BASE_URL" not in json.dumps(build)


def _deploy_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(FRONTEND_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    assert isinstance(steps, list)
    return steps


def _deploy_step(needle: str) -> dict[str, object]:
    for step in _deploy_steps():
        if needle in str(step.get("name", "")):
            return step
    raise AssertionError(f"no deploy step matching '{needle}'")


def test_the_vercel_cli_is_pinned_rather_than_whatever_published_today() -> None:
    # An unpinned global install makes every production deploy depend on a release nobody
    # reviewed; the pinned version is the one moving part a reproducible deploy removes.
    build = _deploy_step("Deploy to Vercel")
    version = str(build["env"]["VERCEL_CLI_VERSION"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert 'npm i -g "vercel@${VERCEL_CLI_VERSION}"' in str(build["run"])


def test_the_deploy_names_the_project_it_is_meant_to_deploy() -> None:
    """`vercel pull --yes` does not fail when unlinked — it CREATES a project and deploys there.

    The build then succeeds against a brand-new project while FRONTEND_URL keeps serving the old
    one, which is a green deploy that shipped nothing. Naming the project is what prevents it.
    """
    build = _deploy_step("Deploy to Vercel")
    assert build["env"]["VERCEL_ORG_ID"] == "${{ vars.VERCEL_ORG_ID }}"
    assert build["env"]["VERCEL_PROJECT_ID"] == "${{ vars.VERCEL_PROJECT_ID }}"
    # Absent variables render as empty strings, so the guard has to be explicit.
    assert 'test -n "$VERCEL_PROJECT_ID"' in str(build["run"])


def test_the_deploy_asserts_the_permanent_domain_now_serves_this_build() -> None:
    # `vercel deploy` prints the immutable per-deployment URL, not the alias. Without this the
    # job reports success for a deployment the recruiter-facing link never points at.
    step = _deploy_step("aliased to FRONTEND_URL")
    assert step["env"]["FRONTEND_URL"] == "${{ vars.FRONTEND_URL }}"
    assert step["env"]["DEPLOYMENT_URL"] == "${{ steps.deploy.outputs.deployment_url }}"
    assert "vercel inspect" in str(step["run"])
    assert "exit 1" in str(step["run"])


def test_the_proxy_itself_is_exercised_by_the_authenticated_smoke() -> None:
    # Running the same selection against the gateway proves the API; running it through Vercel
    # is the only thing that proves the /api rewrite forwards auth headers and an SSE stream.
    step = _deploy_step("survive the proxy")
    assert step["env"]["SMOKE_BASE_URL"] == "${{ vars.FRONTEND_URL }}"
    script = str(step["run"])
    assert "scripts/smoke_auth_token.py" in script
    # Selected by FILE, not by marker: the ops-probe smoke hits unprefixed paths that the SPA
    # fallback would answer with index.html, which would pass while proving nothing.
    assert "pytest tests/smoke/test_production_auth_smoke.py -m smoke" in script
    assert "pytest -m smoke" not in script
