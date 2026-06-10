"""Unit tests for the config-focused secret guard."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _load_secret_guard() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_no_secrets.py"
    spec = spec_from_file_location("check_no_secrets_for_test", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SECRET_GUARD = _load_secret_guard()


def test_secret_guard_accepts_infisical_placeholder(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "database_password: infisical://fraudlens/dev/backend/DATABASE_URL\n",
        encoding="utf-8",
    )

    assert SECRET_GUARD._scan_yaml(path) == []


def test_secret_guard_rejects_inline_secret_like_value(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("api_key: not-a-placeholder\n", encoding="utf-8")

    findings = SECRET_GUARD._scan_yaml(path)

    assert len(findings) == 1
    assert "api_key" in findings[0]
