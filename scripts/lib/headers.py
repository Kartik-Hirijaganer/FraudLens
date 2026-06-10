"""Summary: Engine for the top-of-file SUMMARY headers (governance rule 2). One
place defines the contract so the read-only validator (scripts/check_headers.py)
and the writer (scripts/update_docs.py) never drift. A header is a Python module
docstring or a TypeScript top-of-file block comment with four sections in order —
Summary, Key classes, Key functions, Notes. The Summary/Notes prose is human-owned;
the Key classes/functions bullet inventories are machine-owned and must list exactly
the public top-level classes/functions (Python AST) or exported class/interface and
function/const names (TypeScript). validate_header reports drift; sync_header rewrites
the inventory lines in place, preserving descriptions.

Key classes:
- (none)

Key functions:
- iter_source_files: yield the source files subject to the header rule.
- extract_header: return a file's header text (module docstring / top block comment).
- code_symbols: return the (classes, functions) a file actually defines/exports.
- validate_header: return a list of human-readable violations for a file.
- sync_header: rewrite a file's inventory lines to match its symbols; report change.

Notes:
- __init__.py, *.d.ts, generated docs, test files, and conftest.py are exempt.
- TypeScript `export type` aliases and default exports are intentionally not tracked.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

SECTION_NAMES: tuple[str, ...] = ("Summary", "Key classes", "Key functions", "Notes")

_SOURCE_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/src", (".py",)),
    ("packages", (".py",)),
    ("frontend/src", (".ts", ".tsx")),
    ("scripts", (".py",)),
)
_SKIP_DIR_PARTS = {
    "node_modules",
    ".venv",
    "dist",
    "coverage",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "tests",
    "test",
    "generated",
}
_SKIP_FILE_RE = re.compile(r"(^test_.*|.*_test|.*\.test)\.(py|ts|tsx)$")
_SECTION_RE = re.compile(r"^(Summary|Key classes|Key functions|Notes)\s*:(.*)$")
_BULLET_RE = re.compile(r"^-\s*([A-Za-z_]\w*)")
_TS_CLASS_RE = re.compile(r"^\s*export\s+(?:abstract\s+)?(?:class|interface)\s+(\w+)", re.MULTILINE)
_TS_FUNC_RE = re.compile(
    r"^\s*export\s+(?:(?:async\s+)?function\s+(\w+)|const\s+(\w+))", re.MULTILINE
)
_NONE_SENTINEL = "(none)"


def _is_exempt(relative: Path) -> bool:
    """Return True when a path is exempt from the header rule."""
    if set(relative.parts) & _SKIP_DIR_PARTS:
        return True
    name = relative.name
    return (
        name in {"__init__.py", "conftest.py"}
        or name.endswith(".d.ts")
        or bool(_SKIP_FILE_RE.match(name))
    )


def iter_source_files(repo_root: Path) -> Iterator[Path]:
    """Yield every source file (.py/.ts/.tsx) that must carry a SUMMARY header."""
    for rel, exts in _SOURCE_ROOTS:
        base = repo_root / rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if (
                path.is_file()
                and path.suffix in exts
                and not _is_exempt(path.relative_to(repo_root))
            ):
                yield path


def _python_docstring_span(text: str) -> tuple[int, int] | None:
    """Return the (start, end) line numbers (1-based, inclusive) of the module docstring."""
    module = ast.parse(text)
    doc = ast.get_docstring(module, clean=False)
    if doc is None or not module.body:
        return None
    first = module.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return first.value.lineno, first.value.end_lineno or first.value.lineno
    return None


def _ts_block_span(text: str) -> tuple[int, int] | None:
    """Return the (start, end) line numbers of the leading /* ... */ block comment."""
    stripped = text.lstrip()
    if not stripped.startswith("/*"):
        return None
    offset = len(text) - len(stripped)
    end = text.find("*/", offset)
    if end == -1:
        return None
    start_line = text.count("\n", 0, offset) + 1
    end_line = text.count("\n", 0, end) + 1
    return start_line, end_line


def extract_header(path: Path) -> str | None:
    """Return the header text for a file, or None when it has no recognizable header."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        span = _python_docstring_span(text)
        if span is None:
            return None
        return ast.get_docstring(ast.parse(text), clean=False)
    span = _ts_block_span(text)
    if span is None:
        return None
    lines = text.splitlines()[span[0] - 1 : span[1]]
    return "\n".join(lines)


def _normalize(header: str) -> list[str]:
    """Strip TS comment scaffolding (/**, */, leading *) to plain text lines."""
    out: list[str] = []
    for raw in header.splitlines():
        line = raw.strip()
        if line in {"/**", "/*", "*/"}:
            continue
        line = re.sub(r"^\*\s?", "", line)
        line = re.sub(r"\s*\*/\s*$", "", line)
        out.append(line.strip())
    return out


def code_symbols(path: Path) -> tuple[list[str], list[str]]:
    """Return (classes, functions) defined (Python) or exported (TypeScript) by a file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        module = ast.parse(text)
        classes = [
            node.name
            for node in module.body
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
        ]
        functions = [
            node.name
            for node in module.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and not node.name.startswith("_")
        ]
        return classes, functions
    classes = _TS_CLASS_RE.findall(text)
    functions = [name for pair in _TS_FUNC_RE.findall(text) for name in pair if name]
    return classes, functions


def _parse_sections(header: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return the ordered section labels present and the bullet names per section."""
    order: list[str] = []
    bullets: dict[str, list[str]] = {}
    current: str | None = None
    for line in _normalize(header):
        match = _SECTION_RE.match(line)
        if match:
            current = match.group(1)
            order.append(current)
            bullets[current] = []
            continue
        if current in {"Key classes", "Key functions"}:
            bullet = _BULLET_RE.match(line)
            if bullet and bullet.group(1) != "none":
                bullets[current].append(bullet.group(1))
            elif line.startswith("-") and _NONE_SENTINEL in line:
                pass
    return order, bullets


def validate_header(path: Path) -> list[str]:
    """Return a list of header violations for a file (empty when the header is correct)."""
    header = extract_header(path)
    if header is None:
        return ["missing top-of-file SUMMARY header"]
    order, bullets = _parse_sections(header)
    violations: list[str] = []
    if order != list(SECTION_NAMES):
        violations.append(f"sections must be exactly {list(SECTION_NAMES)} in order; found {order}")
        return violations
    classes, functions = code_symbols(path)
    for label, listed, actual in (
        ("Key classes", bullets["Key classes"], classes),
        ("Key functions", bullets["Key functions"], functions),
    ):
        missing = sorted(set(actual) - set(listed))
        extra = sorted(set(listed) - set(actual))
        if missing:
            violations.append(f"{label}: missing {missing}")
        if extra:
            violations.append(f"{label}: lists undefined {extra}")
    return violations


def _render_inventory(listed_lines: list[str], names: list[str]) -> list[str]:
    """Rebuild a section's bullets in code order, preserving existing descriptions."""
    existing = {match.group(1): line for line in listed_lines if (match := _BULLET_RE.match(line))}
    if not names:
        return [f"- {_NONE_SENTINEL}"]
    return [existing.get(name, f"- {name}:") for name in names]


def _section_bodies(header_lines: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split normalized header lines into per-section body lines (order preserved)."""
    bodies: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in header_lines:
        match = _SECTION_RE.match(line)
        if match:
            current = match.group(1)
            order.append(current)
            bodies[current] = [line]
        elif current is not None:
            bodies[current].append(line)
    return bodies, order


def sync_header(path: Path) -> bool:
    """Rewrite a file's Key classes/functions inventories to match its symbols.

    Returns True when the file changed. Summary/Notes prose is left untouched; only
    the machine-owned bullet lists are regenerated (in code order, descriptions kept).
    """
    if validate_header(path) == []:
        return False
    header = extract_header(path)
    if header is None:
        return False
    classes, functions = code_symbols(path)
    bodies, order = _section_bodies(_normalize(header))
    if order != list(SECTION_NAMES):
        return False  # structural problems are surfaced by validate, not auto-fixed
    for label, names in (("Key classes", classes), ("Key functions", functions)):
        body = bodies[label]
        head = body[0]
        bullets = [ln for ln in body[1:] if ln.startswith("-")]
        trailing_blank = body[-1] == "" and len(body) > 1
        rebuilt = [head, *_render_inventory(bullets, names)]
        if trailing_blank:
            rebuilt.append("")
        bodies[label] = rebuilt
    rebuilt_body = [ln for label in order for ln in bodies[label]]
    return _write_header(path, rebuilt_body)


def _write_header(path: Path, body_lines: list[str]) -> bool:
    """Replace a file's header body with body_lines (re-applying comment scaffolding)."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        span = _python_docstring_span(text)
        if span is None:
            return False
        rendered = ['"""' + body_lines[0], *body_lines[1:], '"""']
    else:
        span = _ts_block_span(text)
        if span is None:
            return False
        rendered = ["/**"] + [f" * {ln}".rstrip() for ln in body_lines] + [" */"]
    lines = text.splitlines()
    updated = lines[: span[0] - 1] + rendered + lines[span[1] :]
    rewritten = "\n".join(updated) + ("\n" if text.endswith("\n") else "")
    if rewritten == text:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True
