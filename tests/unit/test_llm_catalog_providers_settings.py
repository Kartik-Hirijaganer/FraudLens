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


def test_agent_models_declare_required_tool_and_structured_capabilities(
    tmpdir: object,
) -> None:
    catalog = load_catalog(REPO_ROOT / "config" / "llm" / "catalog.yml")
    agent_refs = {
        "openrouter/x-ai/grok-4.3",
        "openrouter/google/gemini-2.5-flash",
        "openrouter/openai/gpt-5-mini",
        "openrouter/anthropic/claude-sonnet-4.6",
    }

    for ref in agent_refs:
        _provider, _model_id, card = catalog.get(ref)
        assert card.tool_calling is True
        assert card.structured_output is True

    agents_path = Path(str(tmpdir)) / "agents.yml"
    agents_path.write_text(
        """
agents:
  evidence_investigator:
    model: openrouter/x-ai/grok-4.3
    fallbacks: [openrouter/google/gemini-2.5-flash]
    tools: [transaction_history]
  sar_writer:
    model: openrouter/openai/gpt-5-mini
    fallbacks: [openrouter/anthropic/claude-sonnet-4.6]
    tools: []
""",
        encoding="utf-8",
    )

    assert check_llm_catalog._agent_capability_findings(catalog, agents_path) == []
    assert (
        check_llm_catalog._agent_capability_findings(
            catalog,
            agents_path.with_name("absent.yml"),
        )
        == []
    )


def test_agent_capability_gate_reports_missing_flags_refs_and_shape(tmpdir: object) -> None:
    scratch_dir = Path(str(tmpdir))
    catalog = Catalog(
        providers={
            "test": {
                "chat": ModelCard(
                    kind=Kind.CHAT,
                    context_window=8,
                    verified_at="2026-06-10",
                    lifecycle=Lifecycle.GA,
                    callable=True,
                    pricing_basis="per_million_tokens",
                )
            }
        }
    )
    agents_path = scratch_dir / "agents.yml"
    agents_path.write_text(
        """
agents:
  investigator:
    model: test/chat
    fallbacks: [test/missing]
    tools: [lookup]
  malformed: not-a-mapping
""",
        encoding="utf-8",
    )

    findings = check_llm_catalog._agent_capability_findings(catalog, agents_path)

    assert any("lacks structured_output" in finding for finding in findings)
    assert any("lacks tool_calling" in finding for finding in findings)
    assert any("absent from the catalog" in finding for finding in findings)
    assert any("every agent entry" in finding for finding in findings)

    agents_path.write_text("unexpected: true\n", encoding="utf-8")
    assert check_llm_catalog._agent_capability_findings(catalog, agents_path) == [
        f"{agents_path}: expected an agents mapping"
    ]


def test_live_catalog_check_scopes_provider_and_uses_embedding_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = Catalog(
        providers={
            "openrouter": {
                "chat": ModelCard(
                    kind=Kind.CHAT,
                    context_window=8,
                    verified_at="2026-08-17",
                    lifecycle=Lifecycle.GA,
                    callable=True,
                    pricing_basis="per_million_tokens",
                ),
                "embed": ModelCard(
                    kind=Kind.EMBED,
                    context_window=8,
                    verified_at="2026-08-17",
                    lifecycle=Lifecycle.GA,
                    callable=True,
                    pricing_basis="per_million_tokens",
                ),
                "retired": ModelCard(
                    kind=Kind.CHAT,
                    context_window=8,
                    verified_at="2026-08-17",
                    lifecycle=Lifecycle.RETIRED,
                    callable=False,
                    pricing_basis="per_million_tokens",
                ),
            }
        }
    )
    providers = load_providers(REPO_ROOT / "config" / "llm" / "providers.yml")
    endpoints: list[str] = []

    def fetch_models(
        _provider: str,
        _base_url: str,
        _api_key: str,
        *,
        endpoint: str = "models",
    ) -> set[str]:
        endpoints.append(endpoint)
        return {"embed"} if endpoint == "embeddings/models" else {"chat"}

    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.setattr(check_llm_catalog, "_fetch_openai_compatible_models", fetch_models)

    assert (
        check_llm_catalog._live_findings(
            catalog,
            providers,
            provider_names={"openrouter"},
        )
        == []
    )
    assert endpoints == ["models", "embeddings/models"]
    assert check_llm_catalog._live_findings(
        catalog,
        providers,
        provider_names={"missing"},
    ) == ["missing: --live-provider is not configured"]


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
