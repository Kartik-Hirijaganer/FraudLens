# Local PR Check Command

## Context

PR #62 passed the repository's code gates but failed `commitlint` because its title,
`Release/v0.2.0`, did not follow Conventional Commits. The existing `make pre-pr` target runs the
shared `make ci` umbrella, but it does not run every separate pull-request workflow job. This lets a
developer see a green local gate while GitHub still rejects the PR.

## Phase 1 — Mirror the complete PR gate locally

### What

- Add one `make pr-check` command that runs the local equivalents of every PR check: title lint,
  dependency installation, formatting/docs generation, whole-repo CI, changed-file/diff coverage,
  application and conditional base-image builds, Terraform validation, and dependency audits.
- Extract the PR-title policy into a dependency-free script shared by local tooling and GitHub
  Actions.
- Add behavioral tests for accepted/rejected titles and title-source resolution.
- Document the exact command, prerequisites, mutating behavior, and mapping to GitHub checks.

### Why

The local preflight must exercise the same implementation as CI, including checks that are separate
jobs in GitHub Actions. Centralizing the title rule prevents the local command and workflow regex
from diverging later.

### How

1. Implement `scripts/check_pr_title.sh`; accept `PR_TITLE`, an argument, an existing PR title via
   `gh`, or an interactive prompt (in that precedence order).
2. Change `.github/workflows/commitlint.yml` to invoke that script.
3. Add focused Make targets for title validation and the base image, plus a fail-fast `pr-check`
   orchestrator using `BASE_REF` for changed-line checks.
4. Add subprocess-based unit coverage for the shell validator.
5. Update the README and branch-protection/local-development runbooks.
6. Run focused tests, `make docs`, the complete local `make pr-check`, and drift-check for this
   phase. Do not commit or push.

### Acceptance criteria

- `make pr-check PR_TITLE='chore: add local PR preflight'` runs all local equivalents of PR #62's
  applicable GitHub checks and exits non-zero on the first failure.
- `make pr-title-check PR_TITLE='Release/v0.2.0'` reproduces PR #62's `commitlint` failure locally.
- The GitHub `commitlint` job and local title check execute the same validator.
- The base-image build runs only when one of the workflow's path-filter files changed.
- Documentation distinguishes the faster shared gate (`make pre-pr`) from the complete PR
  preflight (`make pr-check`).
