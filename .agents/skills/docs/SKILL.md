---
name: docs
description: Regenerate FraudLens deterministic documentation and the Codex skill mirror, verify freshness, and summarize the resulting diff without committing or pushing.
---

# Docs

Regenerate the artifacts owned by the repository's deterministic documentation workflow.

## When To Use

Use when the user asks to regenerate docs, refresh generated references, synchronize agent skills,
or repair a `docs-check` freshness failure.

## Rules

- Run `make docs`; do not hand-edit generated skill mirrors, header inventories, OpenAPI, ERD, or
  `<!-- AUTOGEN:* -->` architecture regions.
- Hand-authored prose remains outside this command's scope unless the user explicitly asks for it.
- Preserve unrelated changes and never infer permission to record Git history.

## Steps

1. Inspect the current change with `git status --short`.
2. Run `make docs` from the repository root.
3. Run `make docs-check` to prove the generated state is stable.
4. Run `git status --short` and `git diff --stat`.
5. Report changed mirror, header, API, ERD, and architecture artifacts by category.

## Verification

- `.agents/skills/` is byte-identical to `.claude/skills/`.
- `make docs-check` exits zero immediately after regeneration.
- The diff contains only expected deterministic outputs and any pre-existing user changes.

## Never Do

- Never edit `.agents/skills/` or another machine-owned region directly.
- Never commit, push, or tag after regeneration without explicit human permission.
- Never insert secrets, PHI, or non-Mermaid binary diagrams into documentation.
