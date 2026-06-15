# Runbook — Release & Versioning

How FraudLens cuts a release. **Propose-only, human-gated:** tooling computes the next
version and verifies the gate; a human cuts the tag (Golden Rule 1 — no autonomous
tag/push). `v1.0.0` is the first complete, locally-demoable release.

## Versioning

- **SemVer**, driven by **Conventional Commits**. `scripts/next_version.py`
  (`make version-next`) reads commits since the last `v*` tag and proposes the bump:
  `feat` → minor, `fix`/`perf` → patch, `!` marker or `BREAKING CHANGE` footer → major.
  It **never tags** — it prints a JSON proposal for a human to confirm or override.
- **Version lives in seven places, kept in lockstep** (rule 5 — one fact, many readers):
  the root `pyproject.toml`, `backend/pyproject.toml`, the three
  `packages/fraudlens-*/pyproject.toml`, `frontend/package.json`, and
  `backend/src/fraudlens_backend/__init__.py` (`__version__`, which stamps the FastAPI app
  version and the `/api/v1/health` payload). After bumping, run `uv lock` so `uv.lock`
  matches, and keep `frontend/package-lock.json`'s root version in sync (`npm ci` fails on
  a mismatch).
- **`make release-gate`** asserts all seven agree (and, with `--expect <version>`, that
  they equal a target) — see the gate below.

## The release gate (§20 — all required before a tag)

`make release-gate` (→ `scripts/release_gate.py`) verifies the **automatable** invariants
and prints the **human-owned** ones. It is read-only and never tags.

Automatable (gate the exit code):

- **Version consistency** across all seven sources.
- **CHANGELOG** carries a `## [<version>]` section.
- **Release workflow** is tag-triggered, re-runs the CI gate (a tag only ships from green,
  rule 9), and runs git-cliff.
- **git-cliff** parses Conventional Commits (`cliff.toml`).
- **Renovate** keeps major updates human-reviewed (`renovate.json`).
- **Dependency-update gate** runs the same CI on `renovate/*` PR branches.
- **Makefile** defines the umbrella gate targets (`ci`, `docs-check`, `tf-validate`,
  `docker-build`, `local-demo-smoke`).

Human-owned (verified out of band — never auto-passed):

- `make local-demo` on a **clean checkout** boots the stack and prints the URL.
- `make local-demo-smoke` passes on a clean checkout.
- Full **browser UAT**, including model **retrain → promote → rollback**.
- A human **approves the `v<version>` tag/push**.

The full per-PR gate (`make ci` + `make docs-check` + `make tf-validate`) and the docs
freshness check (`make docs-check` covers OpenAPI / ERD / architecture / README /
runbooks / cost) run in CI; the release gate asserts they are **wired**, it does not
re-run them.

## Changelog

`git-cliff` (`cliff.toml`) generates the CHANGELOG from Conventional Commits.

- Preview the pending section locally: `make changelog-unreleased`.
- On a `v*` tag push, `release.yml` regenerates the latest section and attaches it to the
  GitHub release. The committed `CHANGELOG.md` carries a curated section per release.

## Release flow

```
make pre-pr            → fmt → docs → ci (the only writer in the loop)
make release-gate      → version consistency + CHANGELOG + automation wired (read-only)
<human> git tag vX.Y.Z → push tag (explicit approval; Golden Rule 1)
   → release.yml: verify (re-runs make ci via _ci-reusable) → git-cliff notes
                  → publish GitHub release → version stamps for build/deploy
```

The deploy of the tagged image (revision @0% → gated migration → smoke →
promote-or-abort) and rollback are in [`deploy-rollback.md`](deploy-rollback.md) and
[`azure-deploy.md`](azure-deploy.md). Deploy stays inert until the cloud accounts exist.

## Cutting `v1.0.0` (the steps)

1. `make pre-pr` green on the release commit.
2. `make release-gate` (or `make release-gate` with `scripts/release_gate.py --expect 1.0.0`)
   — all automatable checks PASS.
3. Walk the human-owned items: clean-checkout `make local-demo` + `make local-demo-smoke`;
   browser UAT including a model retrain → promote → rollback.
4. A human cuts and pushes the tag: `git tag v1.0.0 && git push origin v1.0.0`.
5. `release.yml` re-verifies the gate, generates notes, and publishes the release.

## Maintenance automation

- **Renovate** (`renovate.json`) opens dependency PRs on `renovate/*` branches (uv, npm,
  Terraform, Actions, Docker). `dependency-update.yml` runs the **same** CI gate on them.
  Minor/patch may auto-merge **only when fully green**; **majors are always human-reviewed**.
  No autonomous commits (Golden Rule 1 / rule 8).
- **Security patches:** Renovate vulnerability PRs run at any time;
  `pip-audit` / `npm audit` / `gitleaks` run in CI.
- **Migrations** are expand/contract and applied **pre-traffic** as a gated deploy step;
  `/readyz` checks the schema version. Backend rollback never needs a DB rollback.

## Rollback

Backend = Container Apps revision traffic-shift (seconds); model = registry-pointer
rollback (no redeploy); frontend = Vercel promote/`vercel rollback`. Procedure in
[`deploy-rollback.md`](deploy-rollback.md).
