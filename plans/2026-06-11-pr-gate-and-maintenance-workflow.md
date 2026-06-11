# PR Gate (changed-files, merge-blocking) + Docs/Release Maintenance Workflow

## Context

Two asks from the maintainer:

1. **A PR gate that runs checks scoped to the changed files and *blocks the merge* on
   failure.** Today nothing is actually changed-file-scoped — `ruff`, `mypy`, `pytest`
   all run repo-wide, and `scripts/coverage.sh` applies a global ≥90% gate (the
   `test-coverage-diff` Make target *claims* to be "changed-file aware in CI" but is not).
   Branch protection is documented in [`docs/runbooks/branch-protection.md`](../docs/runbooks/branch-protection.md)
   but **not applied** (`gh api .../branches/main/protection` → 404), so nothing blocks a
   merge yet.

2. **A workflow the maintainer can run** that (a) updates code-file documentation,
   (b) maintains [`docs/architecture/ARCHITECTURE.md`](../docs/architecture/ARCHITECTURE.md),
   (c) updates the OpenAPI spec, (d) detects feature-vs-bug from Conventional Commits and
   proposes the next SemVer, and (e) updates [`README.md`](../README.md).

### Decisions (confirmed with the maintainer)

- **Hybrid changed-files scope.** Per-file checks (lint, format, header presence,
  secrets, **diff-coverage**) scope to the changed set; **type-check and the full test
  suite stay repo-wide** — they are fast here and catch breakage in files that *import* a
  changed file, which a changed-only check silently misses.
- **The maintenance workflow is a skill, dual-homed** for **Claude Code**
  (`.claude/skills/maintain/`) **and Codex** (`.agents/skills/maintain/`), mirroring the
  existing `drift-check` layout.
- **Version bumping is propose-only.** Compute the next SemVer + Unreleased changelog and
  show it; the human cuts the tag (Golden Rule 1; matches the existing tag-driven
  [`release.yml`](../.github/workflows/release.yml)).
- **Complete the gate.** The current CI matrix runs only `make backend-ci` /
  `make frontend-ci` (lint/format/typecheck/coverage) and **skips** `secrets-scan`,
  `header-check`, `docs-check`, `dup-check`, `llm-catalog-check` that `make ci` includes.
  Wire those into the merge-blocking gate.

### Reuse (no duplication — rule 5)

- `make docs` ([`scripts/update_docs.py`](../scripts/update_docs.py)) already regenerates
  OpenAPI, ERD, the `<!-- AUTOGEN:* -->` regions of ARCHITECTURE.md, and the machine-owned
  header inventory lines. The skill *calls* it rather than re-implementing it.
- Conventional Commits are already enforced (the `commitlint` job) and `git-cliff`
  ([`cliff.toml`](../cliff.toml)) already renders the CHANGELOG. The version step builds on
  these, not beside them.

### FraudLens invariants preserved

No PHI anywhere; nothing here touches tenant queries or auth. New scripts emit only file
paths / version strings / commit subjects (no secrets, no PHI). All new Python carries the
rule-2 SUMMARY header and rule-3 behavioral tests (≥90% branch coverage).

---

## Phase 1 — Changed-files plumbing (shared foundation)

- [ ] Add `scripts/changed_files.py`: given a base ref (default `origin/main`, override via
      `BASE_REF` env), return source files changed vs the merge base, filtered by category
      (`py` / `ts` / `all`) and restricted to the rule's source roots. Pure-function core
      (filter a raw `git diff --name-only --diff-filter=ACMR` list) + a thin git wrapper, so
      it is unit-testable without a repo. Graceful fallback to "all source files" when the
      base ref is absent (e.g. a shallow checkout). SUMMARY header + tests.
- [ ] Makefile: `BASE_REF ?= origin/main`; add `lint-changed`, `format-check-changed`
      (ruff/eslint/prettier over only the changed py/ts files; clean no-op when the set is
      empty). The existing repo-wide `lint` / `format-check` stay as-is.
- [ ] Verify: touch a file, run `make lint-changed` and confirm it scopes to that file.

## Phase 2 — Diff-coverage (the real "changed files" coverage gate)

- [ ] Add `diff-cover` to the `[dependency-groups].dev` group in `pyproject.toml`.
- [ ] Backend: emit Cobertura XML (`--cov-report=xml`) and run
      `diff-cover coverage.xml --compare-branch=$(BASE_REF) --fail-under=90` so **changed/
      added lines** must be ≥90% covered. New `backend-coverage-diff` target.
- [ ] Frontend: vitest lcov reporter + `diff-cover coverage/lcov.info
      --compare-branch=$(BASE_REF) --fail-under=90`. New `frontend-coverage-diff` target.
- [ ] Rewrite the `test-coverage-diff` target to actually perform diff-coverage (fulfilling
      its own comment) and have it call both stacks.
- [ ] Verify locally against a scratch branch with an undertested change.

## Phase 3 — Complete + wire the merge-blocking CI gate

- [ ] `_ci-reusable.yml`: add a `quality` job running the cross-cutting checks
      (`secrets-scan`, `header-check`, `docs-check`, `dup-check`, `llm-catalog-check`). Put
      them in the *reusable* workflow so release/deploy enforce them too (closes the gap
      everywhere, not just on PRs). Surfaces as check `ci / quality`.
- [ ] `ci.yml`: add a `pull_request`-only `changed` job that checks out with
      `fetch-depth: 0`, derives `BASE_REF=origin/${{ github.base_ref }}`, and runs
      `make lint-changed format-check-changed test-coverage-diff`. This is the
      "only on changed files" gate. Surfaces as check `changed`.
- [ ] Update [`docs/runbooks/branch-protection.md`](../docs/runbooks/branch-protection.md):
      required checks become `ci / backend`, `ci / frontend`, `ci / docker-build`,
      `ci / quality`, `changed`, `commitlint`; include the exact `gh api` command to apply
      the ruleset.
- [ ] **Branch protection itself is applied by the maintainer** (outward-facing repo
      config; the active `gh` account is the *work* account, which must never touch this
      personal repo). Provide the command; apply only on explicit go-ahead under the
      `Kartik-Hirijaganer` account.

## Phase 4 — Version-bump core (deterministic, propose-only)

- [ ] Add `scripts/next_version.py`: read the latest `v*` tag, classify commits since it via
      Conventional Commits (`feat`→minor, `fix`/`perf`/`refactor`→patch, `!` or
      `BREAKING CHANGE`→major; no bump when only chore/docs/ci/test/style), and print the
      proposed next version + bump level + categorized commits as JSON. Pure classifier core
      (list of commit messages → bump) + thin git wrapper. SUMMARY header + tests covering
      feat/fix/breaking/no-bump and the first-release (no tag) case.
- [ ] Makefile: `version-next` (prints the proposed bump) and `changelog-unreleased`
      (renders the pending `## [Unreleased]` section via `git cliff --unreleased`). Neither
      tags nor pushes.

## Phase 5 — The `maintain` skill (Claude + Codex)

- [ ] `.claude/skills/maintain/SKILL.md` + `.claude/skills/maintain/agents/openai.yaml`.
- [ ] `.agents/skills/maintain/SKILL.md` + `.agents/skills/maintain/agents/openai.yaml`
      (mirror of the Claude copy — same body, Codex front matter).
- [ ] SKILL body (read-mostly, propose-only, **never commits/tags**):
      1. Determine scope (changed-vs-base by default; whole-repo on request) via
         `scripts/changed_files.py`.
      2. Run `make docs` (deterministic: OpenAPI, ERD, arch AUTOGEN, header inventories).
      3. Refresh the *prose* the generators cannot: the human-owned Summary/Notes of each
         changed file's SUMMARY header + its docstrings; the hand-authored narrative of
         ARCHITECTURE.md; and README.md (currently a one-line stub — high value).
      4. Run `make version-next` + `make changelog-unreleased`; report feature/bug/breaking
         and the proposed SemVer + Unreleased entry.
      5. Run `make docs-check` (+ read-only gates) to confirm sync.
      6. Print a `git diff --stat` summary and the exact `git tag vX.Y.Z` command; **stop**
         before any commit/tag (Golden Rule 1).

## Phase 6 — Wire-up, docs, verification

- [ ] SUMMARY headers (rule 2) on every new script; behavioral tests (rule 3) so the
      changed-file coverage gate stays green on its own additions.
- [ ] Update the Makefile `.PHONY` + `help` lines; keep `make ci` / `make pre-pr` green.
- [ ] Update [`CONTRIBUTING.md`](../CONTRIBUTING.md) / docs for the new targets + skill
      (rule 6: docs stay fresh).
- [ ] Run `make pre-pr` and `drift-check plans/2026-06-11-pr-gate-and-maintenance-workflow.md all`;
      resolve drift. **No commit/push without explicit permission (Golden Rule 1).**

## Phase 7 — PR-on-open automation (summary auto-fill + assignment)

- [x] `scripts/pr_summary.py` (+ tests): categorize the changed paths into client-style
      areas (Backend, Frontend, LLM, Libraries, Infra, Config, CI/CD, Tooling, Tests, Docs,
      Plans, Agent skills, Build/config) and render a minimal `**Changed areas:** …` summary.
      Splicing only touches the `<!-- PR-SUMMARY:auto -->` region (human prose preserved;
      idempotent). Reuses `scripts/lib/gitio`.
- [x] `.github/workflows/pr-on-open.yml`: on `pull_request` opened/synchronize/reopened,
      (1) auto-fill the description with the area summary (vs the base SHA) and (2) assign
      the PR to `Kartik-Hirijaganer` (on `opened`). Same-repo PRs only (fork tokens are
      read-only); `pull-requests`/`issues: write`; **not** a required check (never blocks).
- [x] `.github/pull_request_template.md`: trimmed to the auto-managed `## Summary` region,
      a one-line "What & why", and a single FraudLens security check (CI enforces the mechanical
      gate, so the redundant pre-PR/governance checklists were removed).
- [x] `make pr-summary` previews the summary locally; uses the shared `BASE_REF`.
