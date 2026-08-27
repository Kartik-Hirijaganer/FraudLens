"""Summary: CI gate for the FraudLens LLM catalog/provider registries. It validates
the YAML files against fraudlens-llm schemas, requires trust metadata for callable
models, warns on stale verification dates, and optionally performs best-effort live
model-id checks without printing secrets.

Key classes:
- (none)

Key functions:
- main: Validate the LLM catalog/provider registries and return an exit code.

Notes:
- Offline schema/trust validation is the default; --live is networked and optional.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import yaml

from fraudlens_llm.catalog import Catalog, Kind, Lifecycle, ModelCard, load_catalog
from fraudlens_llm.exceptions import ModelNotFoundError
from fraudlens_llm.providers import Protocol, Providers, load_providers

_DEFAULT_STALE_DAYS = 180
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG = _REPO_ROOT / "config" / "llm" / "catalog.yml"
_DEFAULT_PROVIDERS = _REPO_ROOT / "config" / "llm" / "providers.yml"
_DEFAULT_AGENTS = _REPO_ROOT / "config" / "llm" / "agents.yml"


def main() -> int:
    """Validate the LLM catalog/provider registries and return an exit code."""
    parser = argparse.ArgumentParser(description="Validate LLM catalog trust metadata.")
    parser.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    parser.add_argument("--providers", type=Path, default=_DEFAULT_PROVIDERS)
    parser.add_argument("--agents", type=Path, default=_DEFAULT_AGENTS)
    parser.add_argument("--stale-days", type=int, default=_DEFAULT_STALE_DAYS)
    parser.add_argument("--live", action="store_true", help="Best-effort network model-id check")
    parser.add_argument(
        "--live-provider",
        action="append",
        default=[],
        help="Limit --live checks to one provider name; repeat for multiple providers",
    )
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    providers = load_providers(args.providers)

    findings = _trust_findings(catalog, stale_days=args.stale_days)
    findings.extend(_agent_capability_findings(catalog, args.agents))
    warnings = _freshness_warnings(catalog, stale_days=args.stale_days)
    if args.live:
        findings.extend(
            _live_findings(
                catalog,
                providers,
                provider_names=set(args.live_provider) or None,
            )
        )

    for warning in warnings:
        print(f"WARNING: {warning}")
    for finding in findings:
        print(finding)
    if findings:
        print(f"\ncheck_llm_catalog FAILED: {len(findings)} finding(s)")
        return 1
    print("check_llm_catalog OK: catalog/provider schemas and trust metadata are valid")
    return 0


def _trust_findings(catalog: Catalog, *, stale_days: int) -> list[str]:
    """Return trust metadata findings for callable models."""
    _ = stale_days
    findings: list[str] = []
    for provider, model_id, card in _iter_cards(catalog):
        ref = f"{provider}/{model_id}"
        if card.callable and card.lifecycle not in {Lifecycle.GA, Lifecycle.PREVIEW}:
            findings.append(f"{ref}: callable models must be ga or preview")
        if not card.callable:
            continue
        if card.verified_at is None:
            findings.append(f"{ref}: callable model missing verified_at")
        if not card.source_url:
            findings.append(f"{ref}: callable model missing source_url")
        if card.pricing_basis is None:
            findings.append(f"{ref}: callable model missing pricing_basis")
    return findings


def _freshness_warnings(catalog: Catalog, *, stale_days: int) -> list[str]:
    """Return stale metadata warnings for verified entries."""
    warnings: list[str] = []
    today = date.today()
    for provider, model_id, card in _iter_cards(catalog):
        if card.verified_at is None:
            continue
        age_days = (today - card.verified_at).days
        if age_days > stale_days:
            warnings.append(f"{provider}/{model_id}: verified_at is {age_days} days old")
    return warnings


def _agent_capability_findings(catalog: Catalog, agents_path: Path) -> list[str]:
    """Require capabilities on every primary and fallback model used by an agent role."""
    if not agents_path.exists():
        return []
    try:
        payload = yaml.safe_load(agents_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{agents_path}: agent configuration could not be read ({exc.__class__.__name__})"]
    if not isinstance(payload, dict) or not isinstance(payload.get("agents"), dict):
        return [f"{agents_path}: expected an agents mapping"]

    findings: list[str] = []
    for agent_name, agent_config in payload["agents"].items():
        if not isinstance(agent_name, str) or not isinstance(agent_config, dict):
            findings.append(f"{agents_path}: every agent entry must be a mapping")
            continue
        model = agent_config.get("model")
        fallbacks = agent_config.get("fallbacks", [])
        tools = agent_config.get("tools", [])
        refs = [model, *(fallbacks if isinstance(fallbacks, list) else [])]
        if not isinstance(model, str) or not all(isinstance(ref, str) for ref in refs):
            findings.append(f"agents.{agent_name}: model and fallbacks must be model references")
            continue
        for ref in refs:
            try:
                _provider, _model_id, card = catalog.get(ref)
            except ModelNotFoundError:
                findings.append(f"agents.{agent_name}: model '{ref}' is absent from the catalog")
                continue
            if not card.structured_output:
                findings.append(f"agents.{agent_name}: model '{ref}' lacks structured_output")
            if tools and not card.tool_calling:
                findings.append(f"agents.{agent_name}: model '{ref}' lacks tool_calling")
    return findings


def _live_findings(
    catalog: Catalog,
    providers: Providers,
    *,
    provider_names: set[str] | None = None,
) -> list[str]:
    """Return best-effort live model-id findings for providers exposing /models."""
    findings: list[str] = []
    for provider, config in providers.providers.items():
        if provider_names is not None and provider not in provider_names:
            continue
        if config.protocol != Protocol.OPENAI_COMPATIBLE:
            continue
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            findings.append(f"{provider}: --live requires env var {config.api_key_env}")
            continue
        chat_model_ids = _fetch_openai_compatible_models(
            provider,
            config.base_url,
            api_key,
            endpoint="models",
        )
        if chat_model_ids is None:
            continue
        embed_model_ids = chat_model_ids
        if provider == "openrouter":
            fetched_embed_model_ids = _fetch_openai_compatible_models(
                provider,
                config.base_url,
                api_key,
                endpoint="embeddings/models",
            )
            embed_model_ids = fetched_embed_model_ids or set()
        for model_id, card in catalog.providers.get(provider, {}).items():
            if not card.callable:
                continue
            live_ids = embed_model_ids if card.kind == Kind.EMBED else chat_model_ids
            if model_id not in live_ids:
                findings.append(f"{provider}/{model_id}: not present in live /models response")
    if provider_names is not None:
        unknown = provider_names - set(providers.providers)
        findings.extend(f"{provider}: --live-provider is not configured" for provider in unknown)
    return findings


def _fetch_openai_compatible_models(
    provider: str,
    base_url: str | None,
    api_key: str,
    *,
    endpoint: str = "models",
) -> set[str] | None:
    """Fetch an OpenAI-compatible /models list without printing secrets."""
    if base_url is None:
        return None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/{endpoint}",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"WARNING: {provider}: live check skipped ({exc.__class__.__name__})")
        return None
    data = payload.get("data", [])
    if not isinstance(data, list):
        return set()
    model_ids: set[str] = set()
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            model_ids.add(item["id"])
    return model_ids


def _iter_cards(catalog: Catalog) -> list[tuple[str, str, ModelCard]]:
    """Return catalog cards as a stable list."""
    cards: list[tuple[str, str, ModelCard]] = []
    for provider, models in catalog.providers.items():
        for model_id, card in models.items():
            cards.append((provider, model_id, card))
    return cards


if __name__ == "__main__":
    raise SystemExit(main())
