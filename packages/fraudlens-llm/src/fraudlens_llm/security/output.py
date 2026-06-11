"""Summary: Output sanitization for provider text returned by the LLM client. It
neutralizes script tags, dangerous URL schemes, inline event handlers, dangerous
embedded objects, markdown links with dangerous schemes, and obvious encoded
payload labels.

Key classes:
- (none)

Key functions:
- sanitize_output: Return sanitized text safe for callers.

Notes:
- Raw output is scanned before this sanitizer runs.
"""

from __future__ import annotations

import html
import re

_SCRIPT_TAG_RE = re.compile(r"<\s*/?\s*script\b[^>]*>", re.I)
_EMBED_TAG_RE = re.compile(r"<\s*/?\s*(?:iframe|object|embed|applet)\b[^>]*>", re.I)
_INLINE_EVENT_RE = re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_DANGEROUS_SCHEME_RE = re.compile(r"\b(?:javascript|vbscript|data:text/html)\s*:", re.I)
_DANGEROUS_MARKDOWN_RE = re.compile(
    r"\[([^\]]+)\]\(\s*((?:javascript|vbscript|data:text/html)\s*:[^)]+)\)", re.I
)
_ENCODED_LABEL_RE = re.compile(r"\b(base64|hex encoded|rot13)\s*:\s*([A-Za-z0-9+/=]{24,})", re.I)


def sanitize_output(text: str) -> str:
    """Return provider output with active content neutralized."""
    sanitized = _SCRIPT_TAG_RE.sub("[removed-script]", text)
    sanitized = _EMBED_TAG_RE.sub("[removed-embed]", sanitized)
    sanitized = _INLINE_EVENT_RE.sub("", sanitized)
    sanitized = _DANGEROUS_MARKDOWN_RE.sub(r"\1 (removed unsafe link)", sanitized)
    sanitized = _DANGEROUS_SCHEME_RE.sub("unsafe-scheme:", sanitized)
    sanitized = _ENCODED_LABEL_RE.sub(r"\1: [removed-encoded-payload]", sanitized)
    return html.unescape(sanitized)
