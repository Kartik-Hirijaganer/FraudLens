"""Summary: Operational endpoints used by the deploy platform and smoke tests.
GET /healthz is liveness (the process is up). GET /readyz is readiness: it runs a
set of dependency probes (database / ChromaDB / JWKS / Infisical / active LLM provider / Azure
managed identity when an Azure backend is selected) and returns
200 only when none report "down", else 503 — and, under a live LLM profile, only when
every probe reports "ok". Both are UNPREFIXED (no /api/v1) per the endpoint contract.
The probes are pluggable via a dependency so tests can simulate a degraded dependency;
an unconfigured dependency reports "skipped" rather than failing the process.

Key classes:
- LivenessResponse: body of /healthz.
- DependencyCheck: one dependency's readiness result.
- ReadinessResponse: body of /readyz (overall status + per-dependency checks).

Key functions:
- get_readiness_probes: dependency building the active probe set from app state.
- healthz: liveness handler.
- readyz: readiness handler returning 200/503 from the aggregate.

Notes:
- /readyz sets the HTTP status from the aggregate so platform probes can gate on it.
- The database probe runs a bounded SELECT 1 against the engine on app.state; when no
  DATABASE_URL is configured it reports "skipped" (the app still boots).
- The ChromaDB probe checks the baked RAG index for presence (plan §16 Phase 6): a populated
  index → "ok"; a missing/empty index → "down" when `rag_index_required` (prod bakes the
  index) else "skipped" (dev/local need not have built it yet).
- The JWKS probe checks Supabase Auth reachability only when `auth_jwks_url` is configured.
- The Infisical probe verifies DELIVERY, not reachability: the service never calls Infisical
  (secrets are injected as env by `infisical run` / the CI action / the deploy platform), so
  probing that host would couple pod readiness to an unrelated SaaS and still prove nothing
  about the injection. With `infisical_secrets_delivery = "externally_injected"` it asserts
  every `infisical_required_env_keys` name is present and non-blank — "down" (503) when a
  secret sync fails — and reports "skipped" while no delivery is declared. Its detail carries
  a COUNT, never key names, because /readyz is unauthenticated.
- The LLM probe validates EVERY active cascade stage, resolving each stage's NAMED CONNECTION
  from providers.yml at probe time rather than trusting a static single provider entry: two vLLM
  tiers share one governance entry but must answer on two different injected endpoints, so a
  cascade whose BF16 route is down must not report ready (release 0.5.0 Phase 2).
- A stage whose connection declares `allowed_upstreams` (the hosted ZDR route) additionally has
  its eligibility REVALIDATED from live route metadata rather than trusted from static YAML:
  which upstreams may serve a hosted model changes over time, so a route whose permitted
  upstreams have all disappeared reports down instead of silently drafting through an ungoverned
  one. The ZDR request options themselves ride on every call; readiness proves a permitted
  upstream still exists to honour them.
- The Azure identity probe is registered ONLY when a backend selection actually authenticates with
  the managed identity (`azure_blob` storage or `container_apps_jobs` queue), and it acquires a
  real token for each selected audience. Blob and ARM calls happen on user request, not at boot,
  so without this probe a runtime whose identity plumbing is broken reports ready and fails later
  on the first artifact write or job dispatch. Because the probe set is conditional, the live-mode
  required-check set is derived per selection rather than being a static constant.
- The two REMOTE probes (Supabase JWKS, active LLM provider) are cached for 5 minutes on
  app.state.readiness_probe_cache, so a 30-second platform probe cadence no longer turns
  readiness into thousands of outbound provider calls a day. The database and local ChromaDB
  checks stay per-request: they are cheap and they are the ones that actually fail. Caching
  changes only HOW OFTEN a remote dependency is asked — a cached "down" is still "down", the
  aggregate still fails closed, and the response envelope is unchanged.
- Probes may be sync or async; readyz awaits any awaitable result.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, cast
from urllib import request as url_request

from fastapi import APIRouter, Depends
from pydantic import Field
from starlette.requests import Request
from starlette.responses import Response

from fraudlens_backend.backends.azure import ManagedIdentityTokenProvider
from fraudlens_backend.db.session import ping_database
from fraudlens_backend.models.common import CamelModel
from fraudlens_backend.sar.factory import SarTierConfig, load_sar_llm_config
from fraudlens_backend.settings import AppSettings, _config_anchored
from fraudlens_llm import (
    LlmSettings,
    Protocol,
    Providers,
    get_llm_settings,
    load_providers,
    resolve_base_url,
)

router = APIRouter(tags=["ops"])
_HTTP_OK = 200
_REMOTE_PROBE_CACHE_SECONDS = 300.0
_BASE_LIVE_REQUIRED_CHECKS = frozenset(
    {"database", "chromadb", "supabaseAuth", "infisical", "llmProvider"}
)
_AZURE_IDENTITY_CHECK = "azureIdentity"


def _uses_managed_identity(settings: AppSettings) -> bool:
    """Report whether any selected backend authenticates with the Azure managed identity."""
    return (
        settings.storage_backend == "azure_blob" or settings.queue_backend == "container_apps_jobs"
    )


def _live_required_checks(settings: AppSettings) -> frozenset[str]:
    """Name every probe that must report ok under a live LLM profile, for this backend selection."""
    if _uses_managed_identity(settings):
        return _BASE_LIVE_REQUIRED_CHECKS | {_AZURE_IDENTITY_CHECK}
    return _BASE_LIVE_REQUIRED_CHECKS


ReadinessProbe = Callable[[], "DependencyCheck | Awaitable[DependencyCheck]"]
RemoteProbe = Callable[[], "Awaitable[DependencyCheck]"]


class LivenessResponse(CamelModel):
    """Liveness payload — the process is running and serving."""

    status: str = Field(default="ok", description="Always 'ok' when the process serves.")


class DependencyCheck(CamelModel):
    """Readiness result for a single downstream dependency."""

    name: str = Field(..., description="Dependency name, e.g. 'database'.")
    status: str = Field(..., description="One of 'ok', 'down', or 'skipped'.")
    detail: str | None = Field(default=None, description="Optional, PHI-free detail.")


class ReadinessResponse(CamelModel):
    """Aggregate readiness — overall status plus each dependency's check."""

    status: str = Field(..., description="'ready' when no dependency is 'down'.")
    checks: list[DependencyCheck] = Field(..., description="Per-dependency results.")


def _skipped(name: str) -> DependencyCheck:
    """Return a 'skipped' check for a dependency that is not configured/provisioned."""
    return DependencyCheck(name=name, status="skipped", detail="not configured")


def _remote_probe_cache(request: Request) -> dict[str, tuple[float, DependencyCheck]]:
    """Return the process-local remote-probe cache, creating it on first use."""
    cache = getattr(request.app.state, "readiness_probe_cache", None)
    if cache is None:
        cache = {}
        request.app.state.readiness_probe_cache = cache
    return cache


async def _cached(
    cache: dict[str, tuple[float, DependencyCheck]],
    name: str,
    probe: RemoteProbe,
) -> DependencyCheck:
    """Serve a remote dependency result until its entry expires, then probe again."""
    now = time.monotonic()
    entry = cache.get(name)
    if entry is not None and now < entry[0]:
        return entry[1]
    result = await probe()
    cache[name] = (now + _REMOTE_PROBE_CACHE_SECONDS, result)
    return result


async def _probe_llm_provider(settings: AppSettings, timeout: float) -> DependencyCheck:
    """Probe EVERY active SAR stage's route; a cascade is only ready when all stages are."""
    if settings.llm_mode != "live":
        return _skipped("llmProvider")
    detail = "unconfigured"
    try:
        llm_settings = get_llm_settings()
        sar_config = load_sar_llm_config(_config_anchored(settings.sar_config_file))
        stages = sar_config.stages(settings.sar_profile or None)
        providers = load_providers(llm_settings.providers_path)
        for stage in stages:
            detail = stage.name
            await _probe_stage(stage, providers, llm_settings, timeout)
    except Exception:  # provider/config/reachability failures all fail closed
        return DependencyCheck(name="llmProvider", status="down", detail=detail)
    return DependencyCheck(name="llmProvider", status="ok", detail=detail)


async def _probe_stage(
    stage: SarTierConfig,
    providers: Providers,
    llm_settings: LlmSettings,
    timeout: float,
) -> None:
    """Resolve one stage's named route and assert its endpoint answers; raise otherwise."""
    provider_name, separator, model_id = stage.model.partition("/")
    if not separator:
        raise ValueError("SAR model reference has no provider")
    provider = providers.route(provider_name, stage.connection)
    if provider.protocol != Protocol.OPENAI_COMPATIBLE:
        raise ValueError("SAR stage provider does not expose an OpenAI-compatible models route")
    base_url = resolve_base_url(provider, llm_settings)
    api_key = os.environ.get(provider.api_key_env)
    if not api_key:
        raise ValueError("SAR stage API key was not injected")
    headers = {"Authorization": f"Bearer {api_key}"}
    stage_timeout = min(timeout, provider.timeout_s)
    status = await _fetch_status(f"{base_url.rstrip('/')}/models", stage_timeout, headers=headers)
    if status != _HTTP_OK:
        raise ValueError("SAR stage route did not answer")
    allowed = providers.connection(stage.connection).allowed_upstreams if stage.connection else ()
    if allowed:
        await _revalidate_route_eligibility(
            base_url, model_id, allowed, stage_timeout, headers=headers
        )


async def _revalidate_route_eligibility(
    base_url: str,
    model_id: str,
    allowed_upstreams: tuple[str, ...],
    timeout_seconds: float,
    *,
    headers: dict[str, str],
) -> None:
    """Re-derive a hosted route's live upstream eligibility; raise when none remains permitted."""
    payload = await _fetch_json(
        f"{base_url.rstrip('/')}/models/{model_id}/endpoints", timeout_seconds, headers=headers
    )
    data = payload.get("data")
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise ValueError("SAR stage route metadata is unreadable")
    permitted = {name.casefold() for name in allowed_upstreams}
    served_by = {
        str(endpoint.get("provider_name", "")).casefold()
        for endpoint in endpoints
        if isinstance(endpoint, dict)
    }
    if not served_by.intersection(permitted):
        raise ValueError("SAR stage route has no permitted upstream")


def get_readiness_probes(request: Request) -> list[ReadinessProbe]:
    """Build the active readiness probes from app state (overridable in tests/wiring)."""
    settings = cast(AppSettings, request.app.state.settings)
    engine = getattr(request.app.state, "db_engine", None)
    rag_index_dir = getattr(request.app.state, "rag_index_dir", None)
    timeout = settings.db_connect_timeout_seconds

    async def _database() -> DependencyCheck:
        """Probe DB connectivity; skipped when unconfigured, down on any failure."""
        if engine is None:
            return _skipped("database")
        try:
            await ping_database(engine, timeout_seconds=timeout)
        except Exception:  # any connectivity failure → down; detail stays PHI-free
            return DependencyCheck(name="database", status="down", detail="unreachable")
        return DependencyCheck(name="database", status="ok")

    def _chromadb() -> DependencyCheck:
        """Probe the baked RAG index presence; down only when an index is required (prod)."""
        if rag_index_dir is None:
            return _skipped("chromadb")
        # Lazy import keeps heavy chromadb out of the import graph until /readyz needs it.
        from fraudlens_backend.rag import build_embedder  # noqa: PLC0415
        from fraudlens_ml.rag import index_status  # noqa: PLC0415

        provenance = build_embedder(settings).provenance
        status = index_status(rag_index_dir, settings.rag_collection, provenance)
        if status == "ready":
            return DependencyCheck(name="chromadb", status="ok")
        if settings.rag_index_required:
            return DependencyCheck(name="chromadb", status="down", detail=f"index {status}")
        return DependencyCheck(name="chromadb", status="skipped", detail=f"index {status}")

    async def _supabase_auth() -> DependencyCheck:
        """Probe Supabase JWKS reachability when real JWT verification is configured."""
        if settings.auth_jwks_url is None:
            return _skipped("supabaseAuth")
        try:
            status = await _fetch_status(settings.auth_jwks_url, timeout)
        except OSError:
            return DependencyCheck(name="supabaseAuth", status="down", detail="unreachable")
        if status != _HTTP_OK:
            return DependencyCheck(name="supabaseAuth", status="down", detail="unexpected status")
        return DependencyCheck(name="supabaseAuth", status="ok")

    def _infisical() -> DependencyCheck:
        """Verify Infisical-delivered secrets reached the process env (no outbound call)."""
        if settings.infisical_secrets_delivery == "unconfigured":
            return _skipped("infisical")
        missing = sum(
            1 for key in settings.infisical_required_env_keys if not os.environ.get(key, "").strip()
        )
        if missing:
            # Count only — the response is unauthenticated, so the secret inventory of a
            # deployment never leaks through a readiness body.
            return DependencyCheck(
                name="infisical", status="down", detail=f"{missing} injected secret(s) missing"
            )
        injected = len(settings.infisical_required_env_keys)
        return DependencyCheck(
            name="infisical", status="ok", detail=f"{injected} injected secret(s) present"
        )

    async def _azure_identity() -> DependencyCheck:
        """Prove the managed identity actually issues tokens for every selected Azure backend."""
        provider = ManagedIdentityTokenProvider(settings)
        audiences = []
        if settings.storage_backend == "azure_blob":
            audiences.append(("blob", settings.azure_storage_token_resource))
        if settings.queue_backend == "container_apps_jobs":
            audiences.append(("arm", settings.azure_arm_token_resource))
        for label, resource in audiences:
            try:
                await asyncio.to_thread(provider.token, resource)
            except Exception:  # config, flavor, or endpoint failures all fail closed
                # Label only — the audience is non-secret but the response is unauthenticated,
                # so the detail names which backend is broken and nothing about the identity.
                return DependencyCheck(
                    name=_AZURE_IDENTITY_CHECK, status="down", detail=f"{label} token unavailable"
                )
        return DependencyCheck(
            name=_AZURE_IDENTITY_CHECK, status="ok", detail=f"{len(audiences)} audience(s)"
        )

    infisical_probe = getattr(request.app.state, "infisical_readiness_probe", None)
    cache = _remote_probe_cache(request)
    probes: list[ReadinessProbe] = [
        _database,
        _chromadb,
        lambda: _cached(cache, "supabaseAuth", _supabase_auth),
        infisical_probe or _infisical,
        lambda: _cached(cache, "llmProvider", lambda: _probe_llm_provider(settings, timeout)),
    ]
    if _uses_managed_identity(settings):
        probes.append(lambda: _cached(cache, _AZURE_IDENTITY_CHECK, _azure_identity))
    return probes


async def _fetch_status(
    url: str,
    timeout_seconds: float,
    *,
    headers: dict[str, str] | None = None,
) -> int:
    """Fetch a URL in a worker thread and return the HTTP status code."""

    def _open() -> int:
        request = url_request.Request(url, headers=headers or {})
        with url_request.urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status)

    return await asyncio.to_thread(_open)


async def _fetch_json(
    url: str,
    timeout_seconds: float,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    """Fetch a URL in a worker thread and return its decoded JSON object."""

    def _open() -> dict[str, object]:
        request = url_request.Request(url, headers=headers or {})
        with url_request.urlopen(request, timeout=timeout_seconds) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("SAR stage route metadata is not an object")
        return decoded

    return await asyncio.to_thread(_open)


ProbesDep = Annotated[list[ReadinessProbe], Depends(get_readiness_probes)]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """Liveness probe — 200 while the process is serving."""
    return LivenessResponse()


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(request: Request, response: Response, probes: ProbesDep) -> ReadinessResponse:
    """Readiness probe; live LLM profiles require every dependency to report `ok`."""
    checks: list[DependencyCheck] = []
    for probe in probes:
        result = probe()
        checks.append(await result if inspect.isawaitable(result) else result)
    settings = cast(AppSettings, request.app.state.settings)
    ready = (
        {check.name for check in checks} == _live_required_checks(settings)
        and all(check.status == "ok" for check in checks)
        if settings.llm_mode == "live"
        else all(check.status != "down" for check in checks)
    )
    response.status_code = 200 if ready else 503
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
