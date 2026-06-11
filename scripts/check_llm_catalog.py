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

from fraudlens_llm.catalog import Catalog, Lifecycle, ModelCard, load_catalog
from fraudlens_llm.providers import Protocol, Providers, load_providers

_DEFAULT_STALE_DAYS = 180
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG = _REPO_ROOT / "config" / "llm" / "catalog.yml"
_DEFAULT_PROVIDERS = _REPO_ROOT / "config" / "llm" / "providers.yml"


def main() -> int:
    """Validate the LLM catalog/provider registries and return an exit code."""
    parser = argparse.ArgumentParser(description="Validate LLM catalog trust metadata.")
    parser.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    parser.add_argument("--providers", type=Path, default=_DEFAULT_PROVIDERS)
    parser.add_argument("--stale-days", type=int, default=_DEFAULT_STALE_DAYS)
    parser.add_argument("--live", action="store_true", help="Best-effort network model-id check")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    providers = load_providers(args.providers)

    findings = _trust_findings(catalog, stale_days=args.stale_days)
    warnings = _freshness_warnings(catalog, stale_days=args.stale_days)
    if args.live:
        findings.extend(_live_findings(catalog, providers))

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


def _live_findings(catalog: Catalog, providers: Providers) -> list[str]:
    """Return best-effort live model-id findings for providers exposing /models."""
    findings: list[str] = []
    for provider, config in providers.providers.items():
        if config.protocol != Protocol.OPENAI_COMPATIBLE:
            continue
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            findings.append(f"{provider}: --live requires env var {config.api_key_env}")
            continue
        model_ids = _fetch_openai_compatible_models(provider, config.base_url, api_key)
        if model_ids is None:
            continue
        for model_id in catalog.providers.get(provider, {}):
            if model_id not in model_ids:
                findings.append(f"{provider}/{model_id}: not present in live /models response")
    return findings


def _fetch_openai_compatible_models(
    provider: str,
    base_url: str | None,
    api_key: str,
) -> set[str] | None:
    """Fetch an OpenAI-compatible /models list without printing secrets."""
    if base_url is None:
        return None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
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
