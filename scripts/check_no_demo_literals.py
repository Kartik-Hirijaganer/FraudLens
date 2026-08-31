"""Summary: The portfolio-demo "no duplicated identity" guard (plan §16 Phase 8), completing the
`check_*` family beside `check_no_hardcoding.py` (URLs/IPs/model ids) and `check_no_secrets.py`
(config secrets). It derives its forbidden literals FROM `config/portfolio-demo.yaml` itself —
loading the same validated document every consumer loads — and fails when any of them is restated
in Python, TypeScript, shell, Make, docs, config, workflow, or test sources. Deriving rather than
listing is the point: re-pin the story to a new agency, persona, or model bundle and the guard
re-aims at the new values with no edit here, so a value can never live in two places at once.

Key classes:
- (none)

Key functions:
- forbidden_literals: derive the values that may appear only in the canonical story document.
- iter_offences: yield (path, line, literal) for every forbidden literal restated in a file.
- main: scan the tree and return a process exit code.

Notes:
- Permitted by design: `config/portfolio-demo.yaml` (the canonical source) and `plans/` (historical
  plans record the values they pinned). Nothing else is exempt by path.
- The agency NAME is forbidden UNLESS the config declares it as `research_partition_key` too. That
  key exists precisely to say "this string is also an OFFLINE study partition name", which the
  committed GFP artifact and `scripts/lib/gfp/partitions.py` legitimately hold (ADR-017, Phase 2).
  Rename the agency without renaming the partition and the name becomes forbidden again — the
  exemption is declared in the config, not hand-maintained here.
- The model label is likewise exempt only when `model.shared_with_research` explicitly declares
  that benchmark configs and generated research artifacts may repeat it as provenance.
- The external-id NAMESPACE is forbidden, which covers every id derived from it as a substring;
  reporting the namespace keeps one finding per line instead of twenty near-identical ones.
- The scan is line-based text, not an AST: a demo UUID hardcoded in a comment, a Markdown table, or
  a JSON fixture is exactly as wrong as one in code, and TypeScript/Make/YAML have no AST here.
  A trailing `allow-demo-literal` on the offending line suppresses a reviewed exception.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fraudlens_backend.portfolio_demo import load_portfolio_demo_config

REPO_ROOT = Path(__file__).resolve().parents[1]

# Roots that must never restate a story value. `plans/` is deliberately absent (permitted).
_SCAN_ROOTS: tuple[str, ...] = (
    ".github",
    "backend/src",
    "config",
    "docs",
    "frontend/src",
    "packages",
    "scripts",
    "tests",
)
_SCAN_FILES: tuple[str, ...] = ("Makefile",)
_SCAN_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".md", ".yaml", ".yml", ".json", ".toml"}
)
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
    }
)

# The ONE canonical home of every value below; permitted, and the file this checker reads.
_CANONICAL_CONFIG = "config/portfolio-demo.yaml"
_SUPPRESS = "allow-demo-literal"


def forbidden_literals() -> tuple[str, ...]:
    """Derive, from the story document, the values that may appear nowhere else.

    Longest first, so a line holding a persona email reports the email rather than the agency slug
    embedded in its domain.
    """
    config = load_portfolio_demo_config()
    values = {
        str(config.agency.id),
        config.agency.slug,
        config.external_id_namespace,
    }
    # See the module note: a name that is ALSO the offline study partition key is a shared study
    # concept, not a runtime-only identity, so the config's own declaration exempts it.
    if config.agency.name != config.agency.research_partition_key:
        values.add(config.agency.name)
    if not config.model.shared_with_research:
        values.add(config.model.version_label)
    for persona in config.personas:
        values.update({str(persona.seed_user_id), persona.email})
    return tuple(sorted(values, key=len, reverse=True))


def _scanned_paths() -> Iterator[Path]:
    """Yield every source file in scope, skipping generated/vendored trees and the canonical doc."""
    canonical = (REPO_ROOT / _CANONICAL_CONFIG).resolve()
    for name in _SCAN_FILES:
        candidate = REPO_ROOT / name
        if candidate.is_file():
            yield candidate
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.resolve() == canonical:
                continue
            yield path


def iter_offences(path: Path, literals: tuple[str, ...]) -> Iterator[tuple[Path, int, str]]:
    """Yield (path, line number, literal) for each forbidden value restated in a file."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:  # pragma: no cover - a binary file in a scanned suffix
        return
    for number, line in enumerate(text.splitlines(), start=1):
        if _SUPPRESS in line:
            continue
        match = next((literal for literal in literals if literal in line), None)
        if match is not None:
            yield path, number, match


def main() -> int:
    """Scan the tree for restated portfolio-demo values; return 1 if any are found, else 0."""
    literals = forbidden_literals()
    findings = [
        f"{found.relative_to(REPO_ROOT)}:{line}: "
        f"portfolio-demo value '{literal}' is restated — read it from "
        f"{_CANONICAL_CONFIG} instead"
        for path in _scanned_paths()
        for found, line, literal in iter_offences(path, literals)
    ]
    for finding in findings:
        print(finding)
    if findings:
        print(f"\ncheck_no_demo_literals FAILED: {len(findings)} restated portfolio-demo value(s)")
        return 1
    print(
        f"check_no_demo_literals OK: {len(literals)} story value(s) live "
        f"only in {_CANONICAL_CONFIG}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
