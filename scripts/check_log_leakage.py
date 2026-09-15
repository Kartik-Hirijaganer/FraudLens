"""Summary: Refuse a deploy whose captured application logs leak an injected secret value, a bearer
token, a PHI-shaped substring, or a raw stack trace. The gateway already redacts all four
(`fraudlens_backend.middleware.logging`), so this is the post-deploy proof that the redaction
actually ran in production rather than an assertion repeated from the unit suite. Findings are
reported as CATEGORIES and secret NAMES only — the offending value is never printed, because the
report itself would otherwise become the leak.

Key classes:
- LeakReport: the categories a scan found, carrying counts and never a matched value.
- LogLeakageError: safe scan failure (unreadable input or an undeclared secret name).

Key functions:
- scan_log: return the leak categories present in one captured log body.
- main: scan a log file against the named secret environment variables and gate on the result.

Notes:
- PHI detection reuses the gateway's own `scrub_text`: a log that the scrubber would still change
  is, by definition, a log the scrubber failed to clean. No second pattern list exists to drift.
- Secret values are read from environment variable NAMES passed on the command line, so no value
  ever reaches argv, a process listing, or this file.
- A JWT is matched structurally (three base64url segments after the `eyJ` header prefix), which
  catches an access token echoed into a log line whatever field name carried it.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.middleware.logging import scrub_text

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_TRACEBACK_MARKERS = ("Traceback (most recent call last)", '  File "')
_MIN_SECRET_LENGTH = 8

PHI = "phi"
JWT = "jwt"
STACK_TRACE = "stack_trace"


class LogLeakageError(RuntimeError):
    """Raised when the log cannot be read or a declared secret name is not injected."""


class LeakReport(BaseModel):
    """What a scan found — categories and secret names only, never a matched value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: tuple[str, ...] = Field(
        default=(), description="Leak categories found, sorted and deduplicated."
    )
    secret_names: tuple[str, ...] = Field(
        default=(), description="Environment variable NAMES whose value appeared in the log."
    )
    scanned_lines: int = Field(default=0, ge=0, description="Number of log lines scanned.")

    @property
    def clean(self) -> bool:
        """True when nothing leaked."""
        return not self.categories and not self.secret_names


def scan_log(text: str, secrets: dict[str, str]) -> LeakReport:
    """Return the leak categories and leaked secret names present in one captured log body."""
    categories: set[str] = set()
    if scrub_text(text) != text:
        categories.add(PHI)
    if _JWT_RE.search(text):
        categories.add(JWT)
    if any(marker in text for marker in _TRACEBACK_MARKERS):
        categories.add(STACK_TRACE)
    leaked = {name for name, value in secrets.items() if value and value in text}
    return LeakReport(
        categories=tuple(sorted(categories)),
        secret_names=tuple(sorted(leaked)),
        scanned_lines=len(text.splitlines()),
    )


def _resolve_secrets(names: Sequence[str], environ: dict[str, str]) -> dict[str, str]:
    """Return each declared name's injected value, refusing a name with nothing behind it."""
    resolved: dict[str, str] = {}
    for name in names:
        value = environ.get(name, "").strip()
        if len(value) < _MIN_SECRET_LENGTH:
            raise LogLeakageError(
                f"{name} is not injected, so scanning for its value would pass vacuously"
            )
        resolved[name] = value
    return resolved


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refuse secret, JWT, PHI, or stack-trace leakage in captured deploy logs."
    )
    parser.add_argument("log", type=Path, help="File holding the captured application log.")
    parser.add_argument(
        "--secret-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Environment variable NAME whose injected value must not appear in the log.",
    )
    return parser


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    """Scan a captured log against the named secrets and gate the deploy on the result."""
    args = _build_parser().parse_args(argv)
    resolved_env = dict(environ if environ is not None else os.environ)
    try:
        secrets = _resolve_secrets(args.secret_env, resolved_env)
        text = args.log.read_text(encoding="utf-8", errors="replace")
    except LogLeakageError as error:
        print(f"log leakage check failed: {error}")
        return 2
    except OSError:
        print(f"log leakage check failed: {args.log} is unreadable")
        return 2
    report = scan_log(text, secrets)
    if report.clean:
        print(f">> no leakage in {report.scanned_lines} log line(s)")
        return 0
    if report.categories:
        print(f"log leakage check failed: categories {list(report.categories)}")
    if report.secret_names:
        print(f"log leakage check failed: injected secrets echoed {list(report.secret_names)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
