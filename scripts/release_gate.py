"""Summary: Assert the §20 release gate before a `v*` tag is cut — the read-only,
propose-only verifier behind `make release-gate` and the Phase 15 release checklist. It
checks the release-readiness invariants that are unique to cutting a release and NOT
already covered by `make ci`: every workspace package + the frontend + the API
`__version__` agree on ONE version (and match an optional `--expect` target), the
CHANGELOG carries a section for that version, and the §20 maintenance automation is
WIRED — the release workflow re-runs the CI gate so a tag only ships from green
(rule 9), git-cliff drives the changelog, Renovate keeps majors human-reviewed, the
dependency-update gate runs CI on `renovate/*` branches, and the umbrella gate targets
(`ci` / `docs-check` / `tf-validate` / `docker-build` / `local-demo-smoke` /
`local-release-check`) exist. It NEVER tags, commits, or pushes (Golden Rule 1) and it
does NOT run the CI gate (that is CI's job and would be circular) — it asserts the
gate's STRUCTURE plus version readiness, then prints the human-only gate items
(clean-checkout `make local-release-check`, browser UAT incl. model
retrain/promote/rollback, the `v1.0.0` tag approval) so none is silently skipped.

Key classes:
- (none)

Key functions:
- collect_versions: read every declared version (packages + frontend + __version__).
- check_versions: assert all declared versions are present, agree, and match --expect.
- check_changelog: assert the CHANGELOG has a section for the release version.
- check_release_workflow: assert release.yml is tag-triggered, re-runs CI, runs git-cliff.
- check_renovate: assert Renovate keeps major updates human-reviewed.
- check_dependency_update: assert the dependency-update gate re-runs CI on renovate branches.
- check_changelog_config: assert git-cliff parses Conventional Commits.
- check_make_targets: assert the §20 umbrella gate + local release targets exist in the Makefile.
- evaluate: assemble the full gate result (automatable checks + the manual checklist).
- main: CLI entry; print the gate result and exit non-zero if any automatable check fails.

Notes:
- Automatable checks gate the exit code; MANUAL items are reported as required-human and
  never auto-passed — a browser UAT and the human tag approval
  cannot be proven from repository state and are out-of-band by design.
- IO is injected (a repo-relative reader returning '' for missing files) so the logic is
  unit-tested against fixtures without touching the real tree; the script only ever READS.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

# A reader maps a repo-relative path to its text, returning '' when the file is absent.
Reader = Callable[[str], str]

# scripts/release_gate.py -> repo root is one level up.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Every place a release version is declared: (repo-relative path, how to parse it). All of
# these must agree for the release to be coherent (rule 5: one fact, not many copies).
_VERSION_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("root pyproject", "pyproject.toml", "toml"),
    ("backend pyproject", "backend/pyproject.toml", "toml"),
    ("fraudlens-core pyproject", "packages/fraudlens-core/pyproject.toml", "toml"),
    ("fraudlens-llm pyproject", "packages/fraudlens-llm/pyproject.toml", "toml"),
    ("fraudlens-ml pyproject", "packages/fraudlens-ml/pyproject.toml", "toml"),
    ("frontend package.json", "frontend/package.json", "json"),
    ("backend __version__", "backend/src/fraudlens_backend/__init__.py", "dunder"),
)

# §20 umbrella gate targets that must exist in the Makefile (local = CI = deploy).
_REQUIRED_MAKE_TARGETS: tuple[str, ...] = (
    "ci",
    "docs-check",
    "tf-validate",
    "docker-build",
    "local-demo-smoke",
    "local-release-check",
)

_DUNDER_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")

# The release-gate items that CANNOT be proven from repository state and stay human-owned
# (Phase 15 acceptance / §20). Reported, never auto-passed.
MANUAL_GATE_ITEMS: tuple[str, ...] = (
    "`make local-release-check` passes on a clean checkout",
    "`make local-demo` boots the stack and prints the URL for browser UAT",
    "full browser UAT, including model retrain -> promote -> rollback",
    "human approves the `v<version>` tag/push (Golden Rule 1 — no autonomous tagging)",
)


def _extract_version(text: str, kind: str) -> str | None:
    """Pull the declared version from file text, or None when absent/unparseable."""
    if not text:
        return None
    try:
        if kind == "toml":
            value = tomllib.loads(text).get("project", {}).get("version")
            return value if isinstance(value, str) else None
        if kind == "json":
            value = json.loads(text).get("version")
            return value if isinstance(value, str) else None
        if kind == "dunder":
            match = _DUNDER_VERSION_RE.search(text)
            return match.group(1) if match else None
    except (tomllib.TOMLDecodeError, json.JSONDecodeError):
        return None
    return None


def collect_versions(read: Reader) -> dict[str, str | None]:
    """Read the declared version from every source (None when missing/unparseable)."""
    return {name: _extract_version(read(path), kind) for name, path, kind in _VERSION_SOURCES}


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    """Build one gate-check record."""
    return {"name": name, "ok": ok, "detail": detail}


def check_versions(versions: dict[str, str | None], expected: str | None = None) -> dict[str, Any]:
    """Assert every version source is present, they all agree, and match expected (if given)."""
    missing = sorted(name for name, value in versions.items() if value is None)
    if missing:
        return _check("version consistency", False, f"missing version in: {', '.join(missing)}")
    present = [value for value in versions.values() if value is not None]
    distinct = sorted(set(present))
    if len(distinct) != 1:
        return _check("version consistency", False, f"versions disagree: {', '.join(distinct)}")
    version = distinct[0]
    if expected is not None and version != expected:
        return _check("version consistency", False, f"declared {version}, expected {expected}")
    return _check("version consistency", True, f"all {len(versions)} sources at {version}")


def check_changelog(read: Reader, version: str | None) -> dict[str, Any]:
    """Assert the CHANGELOG has a `## [<version>]` section for the release version."""
    if version is None:
        return _check("changelog entry", False, "no consistent version to look up")
    text = read("CHANGELOG.md")
    pattern = re.compile(rf"(?m)^##\s*\[{re.escape(version)}\]")
    ok = bool(pattern.search(text))
    detail = (
        f"CHANGELOG has a [{version}] section" if ok else f"no [{version}] section in CHANGELOG"
    )
    return _check("changelog entry", ok, detail)


def check_release_workflow(read: Reader) -> dict[str, Any]:
    """Assert release.yml is tag-triggered, re-runs the CI gate, and runs git-cliff."""
    text = read(".github/workflows/release.yml")
    tag_triggered = "tags:" in text and "v*" in text
    reruns_ci = "_ci-reusable.yml" in text
    runs_cliff = "git-cliff" in text
    ok = tag_triggered and reruns_ci and runs_cliff
    missing = [
        label
        for label, present in (
            ("tag trigger", tag_triggered),
            ("CI re-run (tag-from-green)", reruns_ci),
            ("git-cliff changelog", runs_cliff),
        )
        if not present
    ]
    detail = (
        "release.yml tag-triggered, re-runs CI, runs git-cliff" if ok else f"missing: {missing}"
    )
    return _check("release workflow", ok, detail)


def check_renovate(read: Reader) -> dict[str, Any]:
    """Assert Renovate exists and keeps major updates human-reviewed (automerge off)."""
    try:
        cfg = json.loads(read("renovate.json"))
    except json.JSONDecodeError:
        return _check("renovate config", False, "renovate.json missing or invalid JSON")
    rules = cfg.get("packageRules", [])
    major_human = any(
        "major" in rule.get("matchUpdateTypes", []) and rule.get("automerge") is False
        for rule in rules
    )
    detail = (
        "major updates require human review"
        if major_human
        else "no rule keeps major updates human-reviewed"
    )
    return _check("renovate config", major_human, detail)


def check_dependency_update(read: Reader) -> dict[str, Any]:
    """Assert the dependency-update gate runs the SAME CI on `renovate/*` PR branches."""
    text = read(".github/workflows/dependency-update.yml")
    ok = "_ci-reusable.yml" in text and "renovate/" in text
    detail = "renovate PRs run the reusable CI gate" if ok else "renovate PR gate not wired"
    return _check("dependency-update gate", ok, detail)


def check_changelog_config(read: Reader) -> dict[str, Any]:
    """Assert git-cliff is configured to read Conventional Commits."""
    text = read("cliff.toml")
    ok = "conventional_commits = true" in text
    detail = "cliff.toml parses conventional commits" if ok else "cliff.toml missing/misconfigured"
    return _check("changelog config", ok, detail)


def check_make_targets(read: Reader) -> dict[str, Any]:
    """Assert the §20 umbrella gate targets are all defined in the Makefile."""
    text = read("Makefile")
    missing = [
        target for target in _REQUIRED_MAKE_TARGETS if not re.search(rf"(?m)^{target}:", text)
    ]
    ok = not missing
    detail = "ci/docs-check/tf-validate/docker-build/local-demo-smoke/local-release-check defined"
    return _check("make gate targets", ok, detail if ok else f"missing targets: {missing}")


def evaluate(read: Reader, *, expected: str | None = None) -> dict[str, Any]:
    """Assemble the full release-gate result: automatable checks + the manual checklist."""
    versions = collect_versions(read)
    version_check = check_versions(versions, expected)
    version = versions["root pyproject"] if version_check["ok"] else None
    checks = [
        version_check,
        check_changelog(read, version),
        check_release_workflow(read),
        check_changelog_config(read),
        check_renovate(read),
        check_dependency_update(read),
        check_make_targets(read),
    ]
    manual = [item.replace("<version>", version or "X.Y.Z") for item in MANUAL_GATE_ITEMS]
    return {
        "version": version,
        "checks": checks,
        "manual": manual,
        "passed": all(check["ok"] for check in checks),
    }


def _repo_reader(rel: str) -> str:
    """Default reader: read a repo-relative file, returning '' when it does not exist."""
    path = _REPO_ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _render_text(result: dict[str, Any]) -> str:
    """Render the gate result as a human-readable checklist."""
    lines = [
        f"Release gate for v{result['version'] or '?'} — "
        f"{'PASS' if result['passed'] else 'FAIL'} (automatable checks)"
    ]
    for check in result["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    lines.append("Manual (human-owned — not auto-verified):")
    lines.extend(f"  [ ] {item}" for item in result["manual"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the release-gate result; exit non-zero if any automatable check fails."""
    parser = argparse.ArgumentParser(
        description="Assert the §20 release gate (propose-only; never tags/pushes)."
    )
    parser.add_argument(
        "--expect",
        default=None,
        help="Require every declared version to equal this target (e.g. 1.0.0).",
    )
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args(argv)
    result = evaluate(_repo_reader, expected=args.expect)
    if args.format == "text":
        print(_render_text(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
