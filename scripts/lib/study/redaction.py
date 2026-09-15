"""Summary: Fail-closed forbidden-content scanning for publishable study artifacts.

Key classes:
- (none)

Key functions:
- scan_forbidden: reject content containing a forbidden token.

Notes:
- Tokens cover common local paths, credentials, and secret-bearing configuration names.
"""

from __future__ import annotations

from collections.abc import Iterable

FORBIDDEN_TOKENS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    "Authorization:",
    "Bearer ",
    "OPENROUTER_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "KAGGLE_API_TOKEN",
)


def scan_forbidden(
    content: str,
    *,
    artifact: str,
    forbidden: Iterable[str] = FORBIDDEN_TOKENS,
) -> None:
    """Raise without echoing the matched value when publishable content is unsafe."""
    if any(token.casefold() in content.casefold() for token in forbidden):
        raise ValueError(f"redaction scan failed for {artifact}: forbidden content present")
