"""Unit tests for layered settings loading and the prod-inert dev bypass."""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_default_and_dev_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    settings = AppSettings()
    # default.yaml supplies app_name + api prefix; dev.yaml overlay flips auth_dev_bypass.
    assert settings.app_name == "FraudLens"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.environment == "dev"
    assert settings.auth_dev_bypass is True  # dev.yaml overlay overrides default.yaml


def test_env_var_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_LOG_LEVEL", "WARNING")
    assert AppSettings().log_level == "WARNING"


def test_prod_overlay_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    settings = AppSettings()
    assert settings.environment == "prod"
    assert settings.auth_dev_bypass is False
    assert settings.allow_candidate_scoring_in_dev is False


def test_config_dir_override_empty_uses_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    with tempfile.TemporaryDirectory() as empty_dir:  # contains no yaml files
        monkeypatch.setenv("FRAUDLENS_CONFIG_DIR", empty_dir)
        settings = AppSettings()
    assert settings.app_name == "FraudLens"  # pure field default (no yaml loaded)
    assert settings.environment == "dev"
    assert settings.auth_dev_bypass is False


def testfind_config_dir_walks_up_from_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    with tempfile.TemporaryDirectory() as scratch:
        monkeypatch.chdir(scratch)  # cwd has no config/; must walk up from the module path
        assert find_config_dir() == REPO_ROOT / "config"


def test_config_dir_override_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as override_dir:
        monkeypatch.setenv("FRAUDLENS_CONFIG_DIR", override_dir)
        assert find_config_dir() == Path(override_dir)


def test_config_anchored_rejects_absolute_and_traversing_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    assert _config_anchored("llm/sar.yml") == REPO_ROOT / "config" / "llm" / "sar.yml"
    with pytest.raises(ValueError, match="relative"):
        _config_anchored(str(REPO_ROOT / "config" / "llm" / "sar.yml"))
    with pytest.raises(ValueError, match="remain below"):
        _config_anchored("../pyproject.toml")


def test_dev_bypass_is_inert_in_prod() -> None:
    assert AppSettings(environment="prod", auth_dev_bypass=True).is_dev_bypass_enabled is False
    assert AppSettings(environment="dev", auth_dev_bypass=True).is_dev_bypass_enabled is True
    assert AppSettings(environment="dev", auth_dev_bypass=False).is_dev_bypass_enabled is False


def test_durable_run_intervals_fail_closed() -> None:
    with pytest.raises(ValidationError, match="shorter than the lease"):
        AppSettings(run_lease_seconds=10, run_heartbeat_seconds=10)
    with pytest.raises(ValidationError, match="poll maximum"):
        AppSettings(run_event_poll_ms=500, run_event_poll_max_ms=499)


def test_candidate_scoring_fallback_is_inert_in_prod() -> None:
    assert (
        AppSettings(
            environment="prod", allow_candidate_scoring_in_dev=True
        ).is_candidate_scoring_fallback_enabled
        is False
    )
    assert (
        AppSettings(
            environment="dev", allow_candidate_scoring_in_dev=True
        ).is_candidate_scoring_fallback_enabled
        is True
    )
    assert (
        AppSettings(
            environment="dev", allow_candidate_scoring_in_dev=False
        ).is_candidate_scoring_fallback_enabled
        is False
    )


def test_boot_config_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    with tempfile.TemporaryDirectory() as empty_dir:  # no yaml -> pure field defaults
        monkeypatch.setenv("FRAUDLENS_CONFIG_DIR", empty_dir)
        settings = AppSettings()
    assert settings.cors_allow_origins == []
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_requests == 120
    assert settings.storage_backend == "local"
    assert settings.queue_backend == "local"
    assert settings.local_job_execute_on_submit is False
    assert settings.allow_candidate_scoring_in_dev is False
    assert settings.azure_arm_endpoint == ""
    assert settings.azure_storage_token_resource == ""
    assert settings.llm_mode == "mock"
    assert settings.sar_config_file == "llm/sar.yml"
    assert settings.rag_embedding_mode == "offline"
    assert settings.investigation_rag_min_similarity == 0.2
    assert settings.database_url is None
    assert settings.auth_role_claim == "user_role"
    assert settings.supabase_url is None
    assert settings.supabase_service_role_key is None
    assert set(settings.security_headers) >= {"X-Content-Type-Options", "X-Frame-Options"}


@pytest.mark.parametrize(
    "origins",
    [[], ["https://fraud-lens-amber.vercel.app"], ["https://a.test", "https://b.test"]],
)
def test_cors_origins_round_trip_through_the_terraform_encoding(
    monkeypatch: pytest.MonkeyPatch, origins: list[str]
) -> None:
    """Whatever `jsonencode(var.cors_allow_origins)` emits must parse back to the same list."""
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_CORS_ALLOW_ORIGINS", _terraform_jsonencode(origins))
    assert AppSettings().cors_allow_origins == origins


@pytest.mark.parametrize("emitted", ["", "https://a.test,https://b.test"])
def test_a_comma_joined_cors_value_aborts_the_boot(
    monkeypatch: pytest.MonkeyPatch, emitted: str
) -> None:
    """`join(",", …)` is what the container used to receive; it must fail loudly, not silently."""
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_CORS_ALLOW_ORIGINS", emitted)
    # pydantic-settings decodes a complex field as JSON before validation runs, so the failure is
    # a SettingsError at source-parse time — the container never reaches a serving state.
    with pytest.raises(SettingsError):
        AppSettings()


def _terraform_jsonencode(value: list[str]) -> str:
    """Reproduce Terraform's jsonencode output: compact JSON with no separator padding."""
    return json.dumps(value, separators=(",", ":"))


def test_dev_overlay_sets_cors_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    settings = AppSettings()
    assert settings.cors_allow_origins == ["http://localhost:5173"]
    assert settings.cors_allow_credentials is True


def test_prod_overlay_selects_cloud_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    settings = AppSettings()
    assert settings.storage_backend == "azure_blob"
    assert settings.queue_backend == "container_apps_jobs"
    assert settings.llm_mode == "live"
    assert settings.rag_embedding_mode == "offline"
    assert settings.azure_managed_identity_token_url.startswith("http://")
    assert settings.azure_arm_endpoint.startswith("https://")
    assert settings.azure_arm_token_resource.startswith("https://")
    assert settings.azure_storage_token_resource.startswith("https://")


def test_prod_caps_one_tenant_day_of_live_llm_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public URL under llm_mode: live needs a dollar bound, not just a request-rate bound."""
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    settings = AppSettings()
    assert settings.llm_daily_budget_usd == Decimal("22.00")
    # The rate limiter alone admits 120 investigations a minute; the ceiling is what bounds the
    # bill, and it holds even if the layered YAML stops declaring it.
    assert AppSettings(environment="prod").llm_daily_budget_usd > 0


def test_a_non_positive_llm_budget_is_rejected_at_boot() -> None:
    """A zero or negative ceiling would be silently uncapped rather than fail-closed."""
    with pytest.raises(ValidationError):
        AppSettings(llm_daily_budget_usd=Decimal("0"))
    with pytest.raises(ValidationError):
        AppSettings(llm_daily_budget_usd=Decimal("-1"))


def test_secret_delivery_defaults_to_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is claimed by default, so the /readyz Infisical check stays informational."""
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    settings = AppSettings(environment="dev")
    assert settings.infisical_secrets_delivery == "unconfigured"
    assert settings.infisical_required_env_keys == []


def test_injected_secret_delivery_requires_at_least_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declaring injection with nothing to verify is rejected at boot (fails closed)."""
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError, match="infisical_required_env_keys"):
        AppSettings(environment="dev", infisical_secrets_delivery="externally_injected")


def test_prod_overlay_declares_verifiable_secret_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config/prod.yaml must make the live-mode Infisical readiness check satisfiable."""
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FRAUDLENS_ENVIRONMENT", "prod")
    settings = AppSettings()
    assert settings.infisical_secrets_delivery == "externally_injected"
    assert "DATABASE_URL" in settings.infisical_required_env_keys
    # Names only — an overlay may never carry a secret VALUE (Golden Rule 3).
    assert all(key.isupper() for key in settings.infisical_required_env_keys)


def test_database_url_read_from_unprefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    assert AppSettings().database_url == "postgresql+asyncpg://u:p@localhost:5432/db"


def test_database_url_accepts_constructor_and_prefixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("FRAUDLENS_DATABASE_URL", "postgresql+asyncpg://x/y")
    assert AppSettings().database_url == "postgresql+asyncpg://x/y"
    assert AppSettings(database_url="postgresql+asyncpg://a/b").database_url == (
        "postgresql+asyncpg://a/b"
    )


def test_supabase_settings_accept_public_url_and_service_role_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "placeholder-service-role")
    settings = AppSettings()
    assert settings.supabase_url == "https://project.supabase.test"
    assert settings.supabase_service_role_key == "placeholder-service-role"
