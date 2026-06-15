"""Summary: The "no hardcoded values" guard (plan §12.1), complementing ruff PLR2004
(magic numbers) and scripts/check_no_secrets.py (config secrets). It parses each Python
source file and flags STRING LITERALS that should live in config/env instead of code:
absolute http(s):// URLs (a scheme followed by a host), IPv4 literals, and LLM model-id
patterns (claude-*, gpt-*, gemini-*, text-embedding-*). Working on the AST (not raw
text) means comments are ignored for free; docstrings are skipped (reference URLs there
are documentation, not configuration); and URLs assembled from f-string parts — e.g.
f"http://{host}:{port}" — never match because the literal fragment is only the scheme.
A trailing `# allow-hardcoded` on the offending line suppresses an intentional case (e.g.
an overridable settings default); the marker avoids clashing with ruff's `# noqa` parser.

Key classes:
- (none)

Key functions:
- iter_offences: yield (path, line, col, reason) for every flagged literal in a file.
- main: scan the source tree and return a process exit code.

Notes:
- Scope is application/library source (backend + packages + scripts); config/, tests/,
  docs/, and generated files are intentionally NOT scanned (they are where values live).
- This checker excludes itself: its own pattern strings are not configuration values.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_SCAN_ROOTS: tuple[str, ...] = (
    "backend/src",
    "packages/fraudlens-core/src",
    "packages/fraudlens-llm/src",
    "packages/fraudlens-ml/src",
    "scripts",
)
_SELF_NAME = "check_no_hardcoding.py"
_SUPPRESS = "allow-hardcoded"

# An absolute URL = scheme + at least one host character (so the bare scheme string
# "https://", used in validators, does NOT match — only real endpoints do).
_URL_RE = re.compile(r"https?://[A-Za-z0-9]")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MODEL_ID_RE = re.compile(
    r"(?i)\b(?:claude-[a-z0-9][\w.-]*|gpt-[0-9][\w.-]*|gemini-[a-z0-9][\w.-]*"
    r"|text-embedding-[a-z0-9][\w.-]*)\b"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_URL_RE, "hardcoded URL — move the host/base URL to config/env"),
    (_IPV4_RE, "hardcoded IP address — move it to config/env"),
    (_MODEL_ID_RE, "hardcoded model id — reference the LLM catalog/config instead"),
)


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """Return the id()s of Constant nodes that are module/class/function docstrings."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def iter_offences(path: Path) -> Iterator[tuple[Path, int, int, str]]:
    """Yield (path, line, col, reason) for each flagged string literal in a file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    docstrings = _docstring_constant_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        line_text = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        if _SUPPRESS in line_text:
            continue
        for pattern, reason in _PATTERNS:
            if pattern.search(node.value):
                yield path, node.lineno, node.col_offset, reason
                break


def main() -> int:
    """Scan the source tree for hardcoded literals; return 1 if any are found, else 0."""
    findings: list[str] = []
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name == _SELF_NAME:
                continue
            for found_path, line, col, reason in iter_offences(path):
                rel = found_path.relative_to(REPO_ROOT)
                findings.append(f"{rel}:{line}:{col}: {reason}")
    for finding in findings:
        print(finding)
    if findings:
        print(f"\ncheck_no_hardcoding FAILED: {len(findings)} hardcoded value(s) in source")
        return 1
    print("check_no_hardcoding OK: no hardcoded URLs/IPs/model-ids in source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
