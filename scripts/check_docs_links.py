"""Summary: Repository-wide relative Markdown link validator for FraudLens docs.
It checks README.md, AGENTS.md, docs/**/*.md, and plans/*.md for missing targets,
repository escapes, and unresolved Markdown heading anchors while ignoring external
URLs and examples inside fenced or inline code.

Key classes:
- LinkIssue: one source-located missing target, unsafe path, or missing anchor.

Key functions:
- iter_markdown_files: yield the exact documentation surface governed by Phase 0.
- extract_relative_links: parse relative Markdown links with source line numbers.
- markdown_anchors: derive GitHub-style heading anchors from a Markdown document.
- validate_docs_links: return every relative-link violation in stable order.
- main: print violations and return a CI-friendly exit code.

Notes:
- Link checking is read-only and never performs network requests.
- Duplicate headings receive GitHub-style numeric suffixes (`heading-1`, `heading-2`).
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\((?P<target><[^>]+>|[^\s)]+)(?:\s+['\"][^)]*['\"])?\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
_MARKDOWN_LINK_TEXT_RE = re.compile(r"!?\[([^]]+)\]\([^)]+\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PUNCTUATION_RE = re.compile(r"[^\w\- ]", flags=re.UNICODE)


class LinkIssue(BaseModel):
    """One source-located documentation link failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(..., description="Repository-relative Markdown source path.")
    line: int = Field(..., gt=0, description="One-based source line containing the link.")
    target: str = Field(..., description="Link target exactly as authored.")
    code: str = Field(..., description="Stable missing-target, escape, or anchor code.")
    message: str = Field(..., description="Actionable human-readable failure detail.")


def iter_markdown_files(repo_root: Path) -> Iterator[Path]:
    """Yield README, AGENTS, docs Markdown, and top-level plan Markdown in stable order."""
    candidates = [repo_root / "README.md", repo_root / "AGENTS.md"]
    candidates.extend((repo_root / "docs").rglob("*.md"))
    candidates.extend((repo_root / "plans").glob("*.md"))
    yield from sorted({item.resolve() for item in candidates if item.is_file()})


def _without_code(lines: Sequence[str]) -> Iterator[tuple[int, str]]:
    """Yield source lines with fenced blocks skipped and inline code removed."""
    fence: str | None = None
    for number, raw in enumerate(lines, start=1):
        stripped = raw.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is None:
            yield number, _INLINE_CODE_RE.sub("", raw)


def extract_relative_links(path: Path) -> Iterator[tuple[int, str]]:
    """Yield `(line, target)` pairs for non-external Markdown links."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in _without_code(lines):
        for match in _LINK_RE.finditer(line):
            authored = match.group("target")
            target = authored[1:-1] if authored.startswith("<") else authored
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("//"):
                continue
            yield number, target


def _heading_slug(text: str) -> str:
    """Approximate GitHub's stable Unicode-aware Markdown heading slug."""
    linked_text = _MARKDOWN_LINK_TEXT_RE.sub(r"\1", text)
    without_html = _HTML_TAG_RE.sub("", linked_text)
    normalized = unicodedata.normalize("NFKC", without_html).strip().lower()
    hyphenated = re.sub(r"\s+", "-", normalized)
    return _PUNCTUATION_RE.sub("", hyphenated)


def markdown_anchors(path: Path) -> set[str]:
    """Return GitHub-style anchors for headings outside fenced code blocks."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for _number, line in _without_code(path.read_text(encoding="utf-8").splitlines()):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        base = _heading_slug(match.group(1))
        count = counts.get(base, 0)
        anchor = base if count == 0 else f"{base}-{count}"
        counts[base] = count + 1
        anchors.add(anchor)
    return anchors


def validate_docs_links(repo_root: Path) -> list[LinkIssue]:
    """Validate all governed relative paths and Markdown anchors."""
    root = repo_root.resolve()
    anchor_cache: dict[Path, set[str]] = {}
    issues: list[LinkIssue] = []
    for source in iter_markdown_files(root):
        relative_source = source.relative_to(root).as_posix()
        for line, authored in extract_relative_links(source):
            parsed = urlsplit(authored)
            relative_path = unquote(parsed.path)
            relative_path = re.sub(r":\d+(?::\d+)?$", "", relative_path)
            target = source if not relative_path else (source.parent / relative_path).resolve()
            root_relative = (root / relative_path).resolve() if relative_path else source
            if not target.exists() and source.parent == root / "plans" and root_relative.exists():
                target = root_relative
            if target != root and root not in target.parents:
                issues.append(
                    LinkIssue(
                        source=relative_source,
                        line=line,
                        target=authored,
                        code="repository_escape",
                        message="relative link resolves outside the repository",
                    )
                )
                continue
            if not target.exists():
                issues.append(
                    LinkIssue(
                        source=relative_source,
                        line=line,
                        target=authored,
                        code="missing_target",
                        message=f"target does not exist: {target.relative_to(root)}",
                    )
                )
                continue
            fragment = unquote(parsed.fragment).strip().lower()
            if not fragment or not target.is_file() or target.suffix.lower() != ".md":
                continue
            anchors = anchor_cache.setdefault(target, markdown_anchors(target))
            if fragment not in anchors:
                issues.append(
                    LinkIssue(
                        source=relative_source,
                        line=line,
                        target=authored,
                        code="missing_anchor",
                        message=f"Markdown heading anchor not found: #{fragment}",
                    )
                )
    return sorted(issues, key=lambda item: (item.source, item.line, item.target))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only repository link check and return one on any issue."""
    parser = argparse.ArgumentParser(description="Validate repository-relative Markdown links.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    issues = validate_docs_links(args.root)
    for issue in issues:
        print(f"{issue.source}:{issue.line}: {issue.code}: {issue.target}: {issue.message}")
    if issues:
        print(f"\ndocs-links-check FAILED: {len(issues)} invalid link(s)")
        return 1
    print("docs-links-check OK: all relative documentation links resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
