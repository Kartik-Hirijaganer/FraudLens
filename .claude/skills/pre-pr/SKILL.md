---
name: pre-pr
description: Run the FraudLens pre-PR gate through the canonical Makefile and summarize failures or generated changes without committing or pushing.
---

# Pre-PR

Run the same write-then-validate gate used before a FraudLens pull request.

## When To Use

Use when the user asks to run the pre-PR checks, prepare changes for review, or verify work before
a commit or pull request.

## Rules

- Treat the root `Makefile` as the single source of truth.
- `make pre-pr` writes through formatting and `make docs`, then runs the read-only `make ci` gate.
- Preserve unrelated working-tree changes and identify generated changes separately.
- A request to run this skill is not permission to commit or push.

## Steps

1. Inspect `git status --short` to establish the active scope before running the gate.
2. Run `make pre-pr` from the repository root.
3. If a target fails, identify the first failing target and its actionable output.
4. When it passes, run `git status --short` and `git diff --stat`.
5. Summarize formatting changes and regenerated skill, header, OpenAPI, ERD, or architecture files.

## Verification

- `make pre-pr` exits zero.
- `make docs-check` is included transitively through `make ci`.
- The final status summary distinguishes pre-existing edits from changes produced by the gate.

## Never Do

- Never bypass a failing target or call the tree ready while a gate is red.
- Never stage, commit, push, tag, or open a PR without explicit human permission.
- Never run a cloud deployment as part of pre-PR verification.
