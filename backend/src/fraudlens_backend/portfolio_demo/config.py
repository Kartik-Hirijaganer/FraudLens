"""Summary: Stable loading facade for the validated portfolio demo story.

Key classes:
- (none)

Key functions:
- load_portfolio_demo_config: load and cache one validated story.
- clear_portfolio_demo_config_cache: clear the process-local loader cache.

Notes:
- Existing model imports remain available through explicit re-exports.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from fraudlens_backend.portfolio_demo.config_models import (
    PortfolioDemoAccent,
    PortfolioDemoAgency,
    PortfolioDemoAuth,
    PortfolioDemoConfigError,
    PortfolioDemoExecution,
    PortfolioDemoExpectation,
    PortfolioDemoModel,
    PortfolioDemoPersona,
    PortfolioDemoProbe,
    PortfolioDemoScenario,
    PortfolioDemoTransaction,
    PortfolioDemoWorkflow,
)
from fraudlens_backend.portfolio_demo.config_story import AUDIT_ACTION, PortfolioDemoConfig
from fraudlens_backend.settings import AppSettings, find_config_dir, get_settings

__all__ = [
    "AUDIT_ACTION",
    "PortfolioDemoAccent",
    "PortfolioDemoAgency",
    "PortfolioDemoAuth",
    "PortfolioDemoConfig",
    "PortfolioDemoConfigError",
    "PortfolioDemoExecution",
    "PortfolioDemoExpectation",
    "PortfolioDemoModel",
    "PortfolioDemoPersona",
    "PortfolioDemoProbe",
    "PortfolioDemoScenario",
    "PortfolioDemoTransaction",
    "PortfolioDemoWorkflow",
    "_load_validated",
    "_resolve_config_path",
    "clear_portfolio_demo_config_cache",
    "load_portfolio_demo_config",
]


def _safe_reason(error: ValidationError) -> str:
    """Summarize a validation failure by field LOCATION and error type only (never the value)."""
    parts: list[str] = []
    for detail in error.errors():
        location = ".".join(str(item) for item in detail["loc"]) or "<root>"
        # `value_error` messages come from the validators above, which are PHI-free by
        # construction; every other type reports its code alone so no input can leak.
        if detail["type"] == "value_error":
            parts.append(f"{location}: {detail['msg']}")
        else:
            parts.append(f"{location}: {detail['type']}")
    return "; ".join(parts)


def _resolve_config_path(config_dir: Path, filename: str) -> Path:
    """Resolve a settings-supplied FILENAME inside the config dir, rejecting any escape."""
    candidate = Path(filename)
    if not filename or filename.startswith("~") or candidate.is_absolute():
        raise PortfolioDemoConfigError(
            "portfolio demo config must be a relative filename under the config directory"
        )
    if ".." in candidate.parts:
        raise PortfolioDemoConfigError("portfolio demo config must not traverse upward")
    base = config_dir.resolve()
    resolved = (base / candidate).resolve()  # follows symlinks, so an escaping link is caught
    if not resolved.is_relative_to(base):
        raise PortfolioDemoConfigError(
            "portfolio demo config resolves outside the config directory"
        )
    if not resolved.is_file():
        raise PortfolioDemoConfigError(f"portfolio demo config '{filename}' is missing")
    return resolved


@lru_cache(maxsize=8)
def _load_validated(target: Path, low_confidence_margin: float) -> PortfolioDemoConfig:
    """Parse + validate one story document, cross-checking the probe against settings."""
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PortfolioDemoConfigError("portfolio demo config could not be read as YAML") from exc
    if not isinstance(raw, dict):
        raise PortfolioDemoConfigError("portfolio demo config must contain a YAML mapping")
    try:
        config = PortfolioDemoConfig.model_validate(raw)
    except ValidationError as exc:
        raise PortfolioDemoConfigError(
            f"portfolio demo config is invalid — {_safe_reason(exc)}"
        ) from exc
    if config.probe.low_confidence_margin != low_confidence_margin:
        raise PortfolioDemoConfigError(
            "probe.low_confidence_margin does not match review_low_confidence_margin"
        )
    return config


def load_portfolio_demo_config(
    path: Path | None = None, *, settings: AppSettings | None = None
) -> PortfolioDemoConfig:
    """Return the validated portfolio demo story (process-cached per path + probe window).

    With no `path`, the location is `AppSettings.portfolio_demo_config_file` resolved under
    `find_config_dir()` with full containment validation. An explicit `path` is operator-supplied
    (tests and the bootstrap's `--config` override) and only has to exist.
    """
    resolved_settings = settings or get_settings()
    if path is None:
        target = _resolve_config_path(
            find_config_dir(), resolved_settings.portfolio_demo_config_file
        )
    elif not path.is_file():
        raise PortfolioDemoConfigError("portfolio demo config path is not a file")
    else:
        target = path.resolve()
    return _load_validated(target, resolved_settings.review_low_confidence_margin)


def clear_portfolio_demo_config_cache() -> None:
    """Drop the per-process cache so the document is parsed again.

    The cache is deliberately not invalidated by an on-disk edit: a running server or CLI keeps
    the story it started with, so nothing changes underneath an in-flight bootstrap or request.
    Restart the process to pick up an edit; tests call this instead.
    """
    _load_validated.cache_clear()
