"""Unit tests for layered settings loading and the prod-inert dev bypass."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from fraudlens_backend.settings import AppSettings, _find_config_dir

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


def test_config_dir_override_empty_uses_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_ENVIRONMENT", raising=False)
    with tempfile.TemporaryDirectory() as empty_dir:  # contains no yaml files
        monkeypatch.setenv("FRAUDLENS_CONFIG_DIR", empty_dir)
        settings = AppSettings()
    assert settings.app_name == "FraudLens"  # pure field default (no yaml loaded)
    assert settings.environment == "dev"
    assert settings.auth_dev_bypass is False


def test_find_config_dir_walks_up_from_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAUDLENS_CONFIG_DIR", raising=False)
    with tempfile.TemporaryDirectory() as scratch:
        monkeypatch.chdir(scratch)  # cwd has no config/; must walk up from the module path
        assert _find_config_dir() == REPO_ROOT / "config"


def test_config_dir_override_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as override_dir:
        monkeypatch.setenv("FRAUDLENS_CONFIG_DIR", override_dir)
        assert _find_config_dir() == Path(override_dir)


def test_dev_bypass_is_inert_in_prod() -> None:
    assert AppSettings(environment="prod", auth_dev_bypass=True).is_dev_bypass_enabled is False
    assert AppSettings(environment="dev", auth_dev_bypass=True).is_dev_bypass_enabled is True
    assert AppSettings(environment="dev", auth_dev_bypass=False).is_dev_bypass_enabled is False
