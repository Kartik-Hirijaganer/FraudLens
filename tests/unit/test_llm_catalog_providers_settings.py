"""Unit tests for LLM catalog, provider governance, settings, and config gates."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from pydantic import ValidationError

from fraudlens_llm import (
    Catalog,
    CatalogError,
    DataClass,
    GenerationParams,
    Intelligence,
    Kind,
    Lifecycle,
    LlmSettings,
    Modality,
    ModelCard,
    PhiMaskingMode,
    Protocol,
    ProviderConfig,
    Speed,
    Strictness,
    load_catalog,
    load_providers,
)
from fraudlens_llm.exceptions import ModelNotFoundError, ProviderNotConfiguredError
from fraudlens_llm.providers import allows_data_class, is_equal_or_stricter

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load script {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_llm_catalog = _load_script("check_llm_catalog")
check_no_secrets = _load_script("check_no_secrets")


def test_load_catalog_get_split_and_select_defaults() -> None:
    catalog = load_catalog(REPO_ROOT / "config" / "llm" / "catalog.yml")

    provider, model_id, card = catalog.get("openrouter/anthropic/claude-sonnet-4.6")

    assert provider == "openrouter"
    assert model_id == "anthropic/claude-sonnet-4.6"
    assert card.callable is True
    selected = catalog.select(
        kind=Kind.CHAT,
        min_intelligence=Intelligence.HIGH,
        modality=Modality.TEXT,
    )
    assert selected[0] == "openrouter/qwen/qwen3.5-plus-02-15"
    assert "ollama/llama3.1" not in selected
    assert "openai/whisper-large-v3" not in selected
    assert "ollama/llama3.1" in catalog.select(include_unverified=True)


def test_catalog_model_lookup_and_validation_errors(tmpdir: object) -> None:
    scratch_dir = Path(str(tmpdir))
    catalog = Catalog(
        providers={
            "test": {
                "model": ModelCard(
                    kind=Kind.CHAT,
                    context_window=8,
                    default_params=GenerationParams(temperature=0.1),
                    source_url="https://example.com",
                    verified_at="2026-06-10",
                    lifecycle=Lifecycle.GA,
                    callable=True,
                    pricing_basis="per_million_tokens",
                    vendor_note="kept by extra allow",
                )
            }
        }
    )

    with pytest.raises(ModelNotFoundError):
        catalog.get("bad-ref")
    assert catalog.providers["test"]["model"].model_extra == {"vendor_note": "kept by extra allow"}
    with pytest.raises(ValidationError):
        GenerationParams(api_key="not-allowed")  # type: ignore[call-arg]
    bad_catalog = scratch_dir / "catalog.yml"
    bad_catalog.write_text("openai:\n  bad:\n    kind: invalid\n", encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(bad_catalog)


def test_provider_schema_governance_and_posture() -> None:
    providers = load_providers(REPO_ROOT / "config" / "llm" / "providers.yml")

    openai = providers.get("openai")
    openrouter = providers.get("openrouter")

    assert allows_data_class(openai, DataClass.RESTRICTED) is True
    assert allows_data_class(openrouter, DataClass.INTERNAL) is False
    assert is_equal_or_stricter(openrouter, openai) is True
    assert is_equal_or_stricter(openai, openrouter) is False
    with pytest.raises(ProviderNotConfiguredError):
        providers.get("ollama")


def test_provider_posture_region_and_retention_edges() -> None:
    current = ProviderConfig(
        protocol=Protocol.OPENAI_COMPATIBLE,
        base_url="https://example.com",
        api_key_env="EXAMPLE_API_KEY",
        timeout_s=1,
        max_retries=0,
        region="us",
        data_retention="30d",
        zdr_supported=False,
        training_opt_out=False,
        baa_required=False,
        allowed_data_classes=[DataClass.SYNTHETIC],
    )
    global_candidate = current.model_copy(update={"region": "global"})
    eu_candidate = current.model_copy(update={"region": "eu"})
    lower_retention = current.model_copy(update={"data_retention": "none"})
    provider_default = current.model_copy(update={"data_retention": "provider-default"})

    assert is_equal_or_stricter(current, global_candidate) is False
    assert is_equal_or_stricter(current, eu_candidate) is False
    assert is_equal_or_stricter(current, lower_retention) is True
    assert is_equal_or_stricter(current, provider_default) is False


def test_provider_validation_rejects_bad_connection_and_headers() -> None:
    base = {
        "protocol": Protocol.OPENAI_COMPATIBLE,
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 30,
        "max_retries": 1,
        "region": "us",
        "data_retention": "30d",
        "zdr_supported": True,
        "training_opt_out": True,
        "baa_required": True,
        "allowed_data_classes": [DataClass.SYNTHETIC],
    }

    with pytest.raises(ValidationError):
        ProviderConfig(**base)
    with pytest.raises(ValidationError):
        ProviderConfig(**base, base_url="http://example.com")
    with pytest.raises(ValidationError):
        ProviderConfig(**{**base, "api_key_env": "not-valid"}, base_url="https://example.com")
    with pytest.raises(ValidationError):
        ProviderConfig(**base, base_url="https://example.com", headers={"Authorization": "x"})
    with pytest.raises(ValidationError):
        ProviderConfig(**base, base_url="https://example.com", headers={"X-Note": "sk-secret"})
    assert (
        ProviderConfig(
            **{**base, "protocol": Protocol.ANTHROPIC},
        ).base_url
        is None
    )


def test_settings_env_overrides_and_prod_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    assert LlmSettings().environment == "prod"
    monkeypatch.setenv("FRAUDLENS_LLM_ENVIRONMENT", "dev")
    monkeypatch.setenv("FRAUDLENS_LLM_DEFAULT_MODEL", "anthropic/claude-haiku-4-5")
    assert LlmSettings().default_model == "anthropic/claude-haiku-4-5"
    assert LlmSettings().environment == "dev"

    with pytest.raises(ValidationError):
        LlmSettings(environment="prod", guardrail_strictness=Strictness.DISABLED)
    with pytest.raises(ValidationError):
        LlmSettings(environment="prod", phi_masking_mode=PhiMaskingMode.OFF)
    with pytest.raises(ValidationError):
        LlmSettings(environment="prod", allow_raw_output=True)
    with pytest.raises(ValidationError):
        LlmSettings(environment="prod", allow_policy_downgrade=True)


def test_config_gates_pass_and_flag_missing_trust_or_inline_secret(tmpdir: object) -> None:
    scratch_dir = Path(str(tmpdir))
    assert check_no_secrets._scan_yaml(REPO_ROOT / "config" / "llm" / "providers.yml") == []
    secret_yaml = scratch_dir / "providers.yml"
    secret_yaml.write_text("provider:\n  api_key: sk-real-looking-value\n", encoding="utf-8")
    assert check_no_secrets._scan_yaml(secret_yaml)

    broken = scratch_dir / "catalog.yml"
    data = yaml.safe_load((REPO_ROOT / "config" / "llm" / "catalog.yml").read_text())
    data["openai"]["gpt-5-mini"].pop("verified_at")
    broken.write_text(yaml.safe_dump(data), encoding="utf-8")
    catalog = load_catalog(broken)
    findings = check_llm_catalog._trust_findings(catalog, stale_days=180)
    assert any("missing verified_at" in finding for finding in findings)


def test_providers_wrapper_rejects_unknown_protocol(tmpdir: object) -> None:
    scratch_dir = Path(str(tmpdir))
    providers_path = scratch_dir / "providers.yml"
    providers_path.write_text(
        """
bad:
  protocol: unsupported
  base_url: https://example.com
  api_key_env: EXAMPLE_API_KEY
  timeout_s: 1
  max_retries: 0
  region: us
  data_retention: none
  zdr_supported: true
  training_opt_out: true
  baa_required: false
  allowed_data_classes: [synthetic]
""",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_providers(providers_path)


def test_catalog_selection_filters_optional_requirements() -> None:
    catalog = load_catalog(REPO_ROOT / "config" / "llm" / "catalog.yml")

    selected = catalog.select(
        kind=Kind.CHAT,
        speed=Speed.FAST,
        provider="openai",
        reasoning_capable=True,
        min_intelligence=Intelligence.MEDIUM,
        max_input_price=0.25,
    )

    assert selected == ["openai/gpt-5-mini"]
