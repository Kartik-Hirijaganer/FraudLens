"""Summary: Security response-header policy for the gateway edge (plan §16 Phase 13,
§4.2, §8.4). This module owns the Content-Security-Policy — the one security header the
foundation deliberately deferred ("Phase 13 adds CSP, which needs care around the Swagger
UI CDN", settings.py) — and the helper that stamps the full configured header set onto
every response. The API surface gets a STRICT policy (`default-src 'none'`) since it serves
only JSON, while the interactive docs UI (Swagger / ReDoc) gets a relaxed policy that allows
the documentation CDN to load its assets. Both policy strings, the toggle, and the docs-path
list are boot-critical config (settings → YAML/env, §12.3), never hardcoded here, so the
posture is fully determined before DB readiness. The gateway delegates ALL security-header
stamping here so there is a single, tested implementation (no duplication, rule 5).

Key classes:
- (none)

Key functions:
- is_docs_path: whether a request path serves the interactive docs UI (relaxed CSP).
- content_security_policy: the CSP string for a path (relaxed on docs, strict elsewhere).
- apply_security_headers: stamp the configured static headers + path-aware CSP on a response.

Notes:
- The strict default (`default-src 'none'; base-uri 'none'; frame-ancestors 'none';
  form-action 'none'`) blocks framing/clickjacking, base-tag hijacking, and form exfiltration
  on the JSON API. The relaxed docs policy is empty by default in code and supplied via config
  (config/default.yaml) so the CDN origin lives in YAML, not source (§12.3 / no-hardcoding).
- When content_security_policy_docs is unset, docs paths fall back to the strict policy — a
  safe default that degrades the docs page but never weakens the API surface.
- This module imports nothing from fraudlens_backend at runtime (AppSettings is referenced
  only for typing), so settings.py and the gateway can depend on it without an import cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.datastructures import MutableHeaders

    from fraudlens_backend.settings import AppSettings

_CSP_HEADER = "Content-Security-Policy"


def is_docs_path(path: str, settings: AppSettings) -> bool:
    """Return True when the path serves the interactive docs UI (Swagger/ReDoc, relaxed CSP)."""
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in settings.docs_ui_paths)


def content_security_policy(path: str, settings: AppSettings) -> str:
    """Return the CSP for a path: the relaxed docs policy on the docs UI, else the strict policy."""
    if is_docs_path(path, settings) and settings.content_security_policy_docs:
        return settings.content_security_policy_docs
    return settings.content_security_policy


def apply_security_headers(headers: MutableHeaders, path: str, settings: AppSettings) -> None:
    """Stamp the configured static security headers plus the path-aware CSP onto a response."""
    for name, value in settings.security_headers.items():
        headers[name] = value
    if settings.csp_enabled:
        headers[_CSP_HEADER] = content_security_policy(path, settings)
