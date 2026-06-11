# Contributing to FraudLens

FraudLens is a personal project run with production-grade hygiene. The canonical rules
live in **[AGENTS.md](AGENTS.md)** (read it first); this is the practical workflow.

## Setup

```bash
make install          # uv sync --all-packages + npm ci (frontend)
```

Prereqs: `uv`, Node 20+, Docker, `gitleaks`, Terraform (for IaC). Python 3.11 is managed
by `uv` via `.python-version`.

## The loop

```bash
make dev              # prints the backend + frontend dev-server commands
# ...make changes...
make pre-pr           # fmt → docs → ci  (the ONLY writer; leaves git clean if green)
```

`make pre-pr` formats, regenerates generated docs, and runs the full read-only gate
(`make ci`). CI mirrors `make ci` exactly, and deploy re-runs it at the deployed SHA — so
**if CI passes, deploy won't fail** (parity). After a non-trivial phase, run `drift-check`.

On a PR, CI also runs a **`changed`** gate that scopes lint, format, and **diff-coverage**
(≥90% on changed lines) to just the files you touched — run it locally with
`make ci-changed` (compares against `origin/main`; override with `BASE_REF=<ref>`).
Type-check and the full test suite stay repo-wide in the `ci / *` jobs, since a change can
break a file that imports it.

## Conventions (enforced)

- **Pydantic at every boundary**; `Field(..., description=...)` on every field.
- **SUMMARY header** on every `.py`/`.ts`/`.tsx` (validated by `make header-check`; the
  Key classes/functions inventory is auto-synced by `make docs`).
- **≥90% coverage**, both stacks; new/changed behavior needs tests.
- **No hardcoded values / no secrets** — non-secret config in `config/*.yaml`/env;
  secrets from Infisical at runtime.
- **No duplication / banned names** (`v2`, `new_`, `temp_`, `tmp_`, `old_`, `legacy_`,
  `copy_`, `_refactored`).
- **Layering**: `fraudlens-core` → nothing internal; `fraudlens-ml` → `core` only;
  `backend` → both (ruff-enforced).
- **Frontend** follows the `wise` design system ([`DESIGN.md`](DESIGN.md)) — tokens only.

## Tests

Copy a template to start:

- Backend: [`tests/unit/_template_test.py`](tests/unit/_template_test.py) →
  `tests/unit/test_<feature>.py` (or `tests/integration/`).
- Frontend: [`frontend/src/test/_template.test.tsx`](frontend/src/test/_template.test.tsx)
  → co-locate as `<Component>.test.tsx`.

Smoke tests (live-URL, deploy gate) live in `tests/smoke/` and are marked `smoke`
(deselected from the normal suite).

## Git & releases

- Branch off `main`; **never commit/push without explicit permission** (Golden Rule 1) —
  this includes bot/Renovate branches.
- **Conventional Commits** (enforced by commitlint on PRs); releases are tag-driven
  (`v*`) with a `git-cliff` CHANGELOG and ship only from CI-green commits.
- Run the **`maintain`** skill (Claude Code / Codex) to refresh code-file docs,
  `ARCHITECTURE.md`, OpenAPI, and the README, and to propose the next SemVer + changelog
  from your commits (`make version-next` / `make changelog-unreleased`). It is
  **propose-only** — it never tags, commits, or pushes; you cut the tag.
- Required PR checks (branch protection) are listed in
  [`docs/runbooks/branch-protection.md`](docs/runbooks/branch-protection.md).
- Open a PR with the template; ensure the checklist + `drift-check` pass.
