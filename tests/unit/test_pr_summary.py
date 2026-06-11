"""Unit tests for scripts/pr_summary.py (the auto PR-description area summary)."""

from __future__ import annotations

from pr_summary import categorize, changed_paths, inject, render_summary


# --------------------------------------------------------------------------- changed_paths
def test_changed_paths_reads_diff_lines() -> None:
    assert changed_paths("origin/main", run=lambda _a: "backend/a.py\nfrontend/b.tsx\n") == [
        "backend/a.py",
        "frontend/b.tsx",
    ]


# --------------------------------------------------------------------------- categorize
def test_categorize_maps_each_area() -> None:
    counts = categorize(
        [
            "backend/src/x.py",
            "frontend/src/y.tsx",
            "packages/fraudlens-llm/src/z.py",
            "packages/fraudlens-core/src/a.py",
            "packages/fraudlens-ml/src/b.py",
            "infra/terraform/m.tf",
            ".github/workflows/ci.yml",
            "scripts/s.py",
            "tests/unit/t.py",
            "docs/d.md",
            "plans/p.md",
            ".claude/skills/maintain/SKILL.md",
            "README.md",
        ]
    )
    assert counts == {
        "Backend": 1,
        "Frontend": 1,
        "LLM": 1,
        "Libraries": 2,  # core + ml grouped
        "Infra": 1,
        "CI/CD": 1,
        "Tooling": 1,
        "Tests": 1,
        "Docs": 1,
        "Plans": 1,
        "Agent skills": 1,
        "Build/config": 1,  # README.md falls back
    }


def test_categorize_llm_not_swallowed_by_libraries() -> None:
    counts = categorize(["packages/fraudlens-llm/src/client.py"])
    assert counts == {"LLM": 1}


# --------------------------------------------------------------------------- render_summary
def test_render_summary_empty() -> None:
    assert render_summary({}) == "_No file changes detected._"


def test_render_summary_orders_by_count_desc_with_plural() -> None:
    out = render_summary({"Backend": 3, "Frontend": 1})
    assert out.splitlines()[0] == "**Changed areas:** Backend · Frontend"
    assert "- **Backend** (3 files)" in out
    assert "- **Frontend** (1 file)" in out  # singular


# --------------------------------------------------------------------------- inject
def test_inject_replaces_marked_region() -> None:
    body = (
        "## Summary\n\n<!-- PR-SUMMARY:auto -->\nold\n<!-- /PR-SUMMARY:auto -->\n\n## What\n\nprose"
    )
    out = inject(body, "NEW")
    assert "NEW" in out
    assert "old" not in out
    assert "## What\n\nprose" in out  # human prose preserved


def test_inject_prepends_when_no_marker() -> None:
    out = inject("just my notes", "AREAS")
    assert out.startswith("<!-- PR-SUMMARY:auto -->\nAREAS\n<!-- /PR-SUMMARY:auto -->")
    assert out.rstrip().endswith("just my notes")


def test_inject_is_idempotent() -> None:
    first = inject("body text", "S1")
    second = inject(first, "S2")
    assert second.count("<!-- PR-SUMMARY:auto -->") == 1
    assert "S2" in second and "S1" not in second
    assert "body text" in second
