# Runbook — Branch protection

Branch protection is applied **manually via repo settings** (Golden Rule 1 — no
automation writes repo config). This documents the intended configuration for `main`.

## Required status checks

Require these checks to pass before merge (they come from `ci.yml` for human PRs and
from `dependency-update.yml` for Renovate PRs — both call the same reusable workflow
with caller job id `ci`, so the names match either way):

- `ci / backend`
- `ci / frontend`
- `ci / docker-build`
- `commitlint` (PR commit-message lint)

Enable **"Require branches to be up to date before merging"** so checks run against the
merge result.

## Recommended settings for `main`

- Require a pull request before merging (≥1 approval; CODEOWNERS review).
- Require status checks to pass (the list above) + up-to-date branches.
- Require conversation resolution.
- Require linear history (optional; pairs well with squash + Conventional Commits).
- Restrict who can push; **no force-push**, no deletion.
- Include administrators.

## Renovate automerge

Patch/minor dependency PRs may **automerge only when fully green** (configured in
`renovate.json`); branch protection's required checks are what "green" means, so Renovate
cannot merge a red PR. **Major** updates always require human review (label `major-update`).

## How to apply

GitHub → repo **Settings → Branches → Add branch ruleset** (or classic branch
protection) for `main`; add the required checks above. Re-check the names after the first
CI run on a PR, since GitHub registers check contexts only once they have run.
