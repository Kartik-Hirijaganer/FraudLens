"""Summary: Strict origin-only URL validation for remote study endpoints.

Key classes:
- (none)

Key functions:
- validate_origin_url: require an HTTP(S) origin without credentials, query, or fragment.

Notes:
- Callers may choose whether plain HTTP is acceptable for local execution.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def validate_origin_url(value: str, *, allow_http_hosts: tuple[str, ...] = ()) -> str:
    """Return a normalized origin or reject non-origin and credential-bearing URLs."""
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("study endpoint must be a valid absolute origin URL") from exc
    allowed_scheme = parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in allow_http_hosts
    )
    if (
        not allowed_scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("study endpoint must be an allowed origin URL")
    return f"{parsed.scheme}://{parsed.netloc}"
