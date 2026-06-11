"""Summary: Build a minimal, client-style PR summary of which areas of the codebase a
branch touched — backend, frontend, LLM, libraries, infra, etc. The pr-autofill workflow
runs this on PR open/update and splices the result into the auto-managed region of the PR
description; `make pr-summary` prints it locally. Categorization is pure (a path-prefix map)
and git access goes through scripts/lib/gitio (an injectable runner), so the logic is
unit-tested without a repo. Splicing only ever touches the marked region, so a human's prose
elsewhere in the description is preserved, and re-runs are idempotent.

Key classes:
- (none)

Key functions:
- changed_paths: the paths changed between a base ref and HEAD.
- categorize: map changed paths to area -> file count (first matching area wins).
- render_summary: render the area counts as a minimal Markdown summary.
- inject: splice the summary into the auto region of a PR body (replace, else prepend).
- main: CLI entry; print the new PR body, or just the summary with --summary-only.

Notes:
- Areas are checked in order, so fraudlens-llm maps to LLM before the generic packages/
  catch-all (Libraries); anything unmatched falls back to Build/config.
- The auto region is delimited by the PR-SUMMARY:auto markers; edits outside it are kept.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from lib.gitio import GitRunner, run_git, run_lines

DEFAULT_BASE = "origin/main"
_MARKER_START = "<!-- PR-SUMMARY:auto -->"
_MARKER_END = "<!-- /PR-SUMMARY:auto -->"
_REGION_RE = re.compile(re.escape(_MARKER_START) + r"\n.*?\n" + re.escape(_MARKER_END), re.DOTALL)

# (label, path-prefixes) checked in order; first match wins. Order matters: LLM before the
# generic "packages/" so the LLM package is not swallowed by Libraries.
_AREAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Backend", ("backend/",)),
    ("Frontend", ("frontend/",)),
    ("LLM", ("packages/fraudlens-llm/",)),
    ("Libraries", ("packages/fraudlens-core/", "packages/fraudlens-ml/", "packages/")),
    ("Infra", ("infra/",)),
    ("Config", ("config/",)),
    ("CI/CD", (".github/",)),
    ("Tooling", ("scripts/",)),
    ("Tests", ("tests/",)),
    ("Docs", ("docs/",)),
    ("Plans", ("plans/",)),
    ("Agent skills", (".claude/", ".agents/")),
)
_FALLBACK = "Build/config"
_ORDER = {name: index for index, (name, _) in enumerate(_AREAS)}
_ORDER[_FALLBACK] = len(_AREAS)


def changed_paths(base: str, *, run: GitRunner = run_git) -> list[str]:
    """Return the paths changed between base and HEAD (added/modified/renamed/deleted)."""
    return run_lines(run, ["diff", "--name-only", "--diff-filter=ACMRD", f"{base}...HEAD"])


def categorize(paths: list[str]) -> dict[str, int]:
    """Map changed paths to {area: file count}; each path counts toward one area."""
    counts: dict[str, int] = {}
    for path in paths:
        area = next((name for name, prefixes in _AREAS if path.startswith(prefixes)), _FALLBACK)
        counts[area] = counts.get(area, 0) + 1
    return counts


def render_summary(counts: dict[str, int]) -> str:
    """Render area counts as a minimal Markdown summary (most-changed area first)."""
    if not counts:
        return "_No file changes detected._"
    items = sorted(counts.items(), key=lambda kv: (-kv[1], _ORDER.get(kv[0], 99)))
    headline = " · ".join(name for name, _ in items)
    lines = [f"**Changed areas:** {headline}", ""]
    lines += [f"- **{name}** ({n} file{'s' if n != 1 else ''})" for name, n in items]
    return "\n".join(lines)


def inject(body: str, summary: str) -> str:
    """Splice summary into the PR body's auto region; replace it if present, else prepend."""
    region = f"{_MARKER_START}\n{summary}\n{_MARKER_END}"
    if _REGION_RE.search(body):
        return _REGION_RE.sub(lambda _m: region, body)
    return f"{region}\n\n{body}" if body.strip() else f"{region}\n"


def main(argv: list[str] | None = None) -> int:
    """Print the new PR body with the summary spliced in (or just the summary)."""
    parser = argparse.ArgumentParser(description="Build a minimal area summary for a PR.")
    parser.add_argument("--base", default=DEFAULT_BASE, help="base ref (default origin/main)")
    parser.add_argument("--body-file", help="path to the current PR body (Markdown)")
    parser.add_argument(
        "--summary-only", action="store_true", help="print only the summary, not a full body"
    )
    args = parser.parse_args(argv)

    summary = render_summary(categorize(changed_paths(args.base)))
    if args.summary_only:
        print(summary)
        return 0
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    print(inject(body, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
