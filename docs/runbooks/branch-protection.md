# Runbook — Branch protection

Branch protection is applied **manually via repo settings** (Golden Rule 1 — no
automation writes repo config). This documents the intended configuration for protected
branches: `main`, `dev`, and `release/*`.

Before opening or updating a PR, run `make pr-check`. It mirrors the complete applicable GitHub PR
gate locally; pass `PR_TITLE='type: subject'` before a PR exists, or let the command read the title
from the current branch's open PR through `gh`.

## Branch policy

| Branch pattern | Purpose | Direct pushes | Deletion | Deploys |
| --- | --- | --- | --- | --- |
| `main` | Stable integration / source of truth | Blocked; PR required | Blocked | No |
| `dev` | Deployment-capable development branch | Blocked; PR required | Blocked | Manual only |
| `release/*` | Deployment-capable release branches | Blocked; PR required | Blocked | Manual only |

Backend and frontend deploy workflows are `workflow_dispatch`-only:
[`deploy-backend.yml`](../../.github/workflows/deploy-backend.yml) and
[`deploy-frontend.yml`](../../.github/workflows/deploy-frontend.yml). A push and its CI run never
start a deployment on any branch. An operator must select the exact `dev` or `release/*` ref in the
Actions UI or `gh workflow run`; the workflow then re-runs the repository CI gate before any cloud
job. Backend dispatches run backend, backend-infra, and repository-governance checks; frontend
dispatches run frontend and repository-governance checks. Full CI and release verification still
run both stacks. The production environment and enable variables remain independent deployment
gates.

## Required status checks

Require these checks to pass before merge. The `ci / *` checks come from `ci.yml` for
human PRs and from `dependency-update.yml` for Renovate PRs — both call the same reusable
workflow with caller job id `ci`, so the names match either way. `changed` and `commitlint`
run on every PR: `changed` is defined in `ci.yml`, while `commitlint` is defined in the separate
`commitlint.yml` workflow so title edits re-run only that fast check.

| Required check | What it validates | Scope |
| --- | --- | --- |
| `ci / backend` | Python lint, format check, type-check, and ≥90% backend coverage | Whole repo backend / Python workspace |
| `ci / frontend` | ESLint, Prettier check, TypeScript type-check, and frontend coverage | Whole frontend app |
| `ci / quality` | SUMMARY headers, secret scan, generated-doc freshness, duplication, and LLM catalog validation | Whole repo |
| `ci / docker-build` | Backend Docker image builds without pushing | Whole repo build context |
| `changed` | Changed-file lint/format plus changed-line diff coverage against the PR base SHA | Changed files / changed lines only |
| `commitlint` | PR title follows Conventional Commits via the same script as `make pr-title-check` | PR metadata only |

Type-check and the full test suite stay in the repo-wide `ci / *` jobs (not `changed`):
scoping them to changed files would miss breakage in files that import a changed file.
`changed` adds the changed-line coverage gate and fast per-file lint/format on top.

Enable **"Require branches to be up to date before merging"** so checks run against the
merge result.

## Recommended settings for protected branches

- Require a pull request before merging (≥1 approval; CODEOWNERS review).
- Require status checks to pass (the list above) + up-to-date branches.
- Require conversation resolution.
- Require linear history (optional; pairs well with squash + Conventional Commits).
- Restrict who can push; **no force-push**, no deletion.
- Enable **Automatically delete head branches** under repo Pull Request settings.
- Include administrators.

## Merged branch cleanup

Same-repo PR branches are deleted after merge by
[`delete-merged-branch.yml`](../../.github/workflows/delete-merged-branch.yml). The workflow
runs only on merged PRs, skips fork branches, refuses to delete the default branch / `main` /
`master` / `dev` / `release/*`, and verifies the branch still points at the merged PR head
SHA before deleting it. If GitHub's native **Automatically delete head branches** setting
already removed the branch, the workflow exits successfully.

Enable the native setting in **Settings → General → Pull Requests → Automatically delete
head branches**. If using `gh` intentionally for repo settings, the equivalent is:

```bash
gh api -X PATCH repos/Kartik-Hirijaganer/FraudLens -f delete_branch_on_merge=true
```

The workflow also requires Actions' `GITHUB_TOKEN` to be allowed `contents: write` for this
repo; otherwise the native GitHub setting still handles branch deletion.

## Renovate automerge

Patch/minor dependency PRs may **automerge only when fully green** (configured in
`renovate.json`); branch protection's required checks are what "green" means, so Renovate
cannot merge a red PR. **Major** updates always require human review (label `major-update`).

## How to apply

GitHub registers a check context only after it has run once, so **open one PR and let CI
finish first**, then apply protection (the context names must already exist).

### Option A — GitHub UI

Repo **Settings → Rules → Rulesets → New branch ruleset**:

1. Target branches: `main`, `dev`, and `release/*`.
2. Enable **Restrict deletions** and **Block force pushes**.
3. Enable **Require a pull request before merging**.
4. Require at least 1 approval and CODEOWNERS review.
5. Enable **Require status checks to pass** and add the checks listed above.
6. Enable **Require branches to be up to date before merging**.
7. Enable **Require conversation resolution**.
8. Enable **Require linear history** if you want squash/rebase-only merges.
9. Include administrators.

### Option B — `gh` API (ruleset)

> **Account:** this is the personal repo `Kartik-Hirijaganer/FraudLens`. The `gh` active
> account is often the **work** account — switch first and never apply this with the work
> account: `gh auth switch --user Kartik-Hirijaganer`.

```bash
gh api -X POST repos/Kartik-Hirijaganer/FraudLens/rulesets --input - <<'JSON'
{
  "name": "protected-pr-only-branches",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main", "refs/heads/dev", "refs/heads/release/*"],
      "exclude": []
    }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_approving_review_count": 1,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "ci / backend" },
          { "context": "ci / frontend" },
          { "context": "ci / quality" },
          { "context": "ci / docker-build" },
          { "context": "changed" },
          { "context": "commitlint" }
        ]
      }
    },
    { "type": "required_linear_history" }
  ],
  "bypass_actors": []
}
JSON
```

Verify with `gh api repos/Kartik-Hirijaganer/FraudLens/rulesets`.
