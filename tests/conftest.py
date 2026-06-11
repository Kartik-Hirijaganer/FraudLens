"""Shared pytest fixtures: settings and TestClient factories for the backend."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fraudlens_backend.main import create_app
from fraudlens_backend.settings import AppSettings

# Put scripts/ on the path so tests can spec-load the maintenance scripts
# (changed_files, next_version) and let them import their `lib.*` helpers.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Test templates are examples to copy, not live tests.
collect_ignore_glob = ["**/_template_test.py"]


@pytest.fixture
def make_settings() -> Callable[..., AppSettings]:
    """Return a factory building AppSettings with explicit, deterministic overrides."""

    def _make(**overrides: Any) -> AppSettings:
        params: dict[str, Any] = {
            "environment": "dev",
            "auth_dev_bypass": False,
            "log_level": "INFO",
        }
        params.update(overrides)
        return AppSettings(**params)

    return _make


@pytest.fixture
def client_factory(
    make_settings: Callable[..., AppSettings],
) -> Callable[..., TestClient]:
    """Return a factory building a TestClient over an app with the given settings."""

    def _make(**overrides: Any) -> TestClient:
        app = create_app(make_settings(**overrides))
        return TestClient(app, raise_server_exceptions=False)

    return _make
