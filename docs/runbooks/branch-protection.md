# Runbook — Branch protection

Branch protection is applied **manually via repo settings** (Golden Rule 1 — no
automation writes repo config). This documents the intended configuration for `main`.

## Required status checks

Require these checks to pass before merge. The `ci / *` checks come from `ci.yml` for
human PRs and from `dependency-update.yml` for Renovate PRs — both call the same reusable
workflow with caller job id `ci`, so the names match either way. `changed` and `commitlint`
are defined directly in `ci.yml` and run on every PR:

- `ci / backend` — backend lint, format, type-check, coverage (repo-wide)
- `ci / frontend` — frontend lint, format, type-check, coverage (repo-wide)
- `ci / quality` — SUMMARY headers, secret scan, doc freshness, duplication, LLM catalog
- `ci / docker-build` — backend image builds
- `changed` — per-file lint/format + **diff-coverage** scoped to the PR's changed files
- `commitlint` — PR title follows Conventional Commits

Type-check and the full test suite stay in the repo-wide `ci / *` jobs (not `changed`):
scoping them to changed files would miss breakage in files that import a changed file.
`changed` adds the changed-line coverage gate and fast per-file lint/format on top.

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

GitHub registers a check context only after it has run once, so **open one PR and let CI
finish first**, then apply protection (the context names must already exist).

### Option A — GitHub UI

Repo **Settings → Branches → Add branch ruleset** (or classic branch protection) for
`main`; add the required checks listed above and the recommended settings.

### Option B — `gh` API (one command)

> **Account:** this is the personal repo `Kartik-Hirijaganer/FraudLens`. The `gh` active
> account is often the **work** account — switch first and never apply this with the work
> account: `gh auth switch --user Kartik-Hirijaganer`.

```bash
gh api -X PUT repos/Kartik-Hirijaganer/FraudLens/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["ci / backend", "ci / frontend", "ci / quality", "ci / docker-build", "changed", "commitlint"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1, "require_code_owner_reviews": true },
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Verify with `gh api repos/Kartik-Hirijaganer/FraudLens/branches/main/protection`.
