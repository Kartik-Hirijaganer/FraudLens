---
name: "source-command-docs"
description: "Regenerate docs (make docs) and summarize the diff; never commit or push."
---

# source-command-docs

Use this skill when the user asks to run the migrated source command `docs`.

## Command Template

Regenerate the generated documentation and report what changed.

1. Run `make docs` (syncs SUMMARY header inventories, OpenAPI at
   `docs/reference/generated/api/`, ERD at `docs/reference/generated/erd/`, and the
   architecture doc's `<!-- AUTOGEN:* -->` regions).
2. Run `git status --short` and `git diff --stat` and summarize the regenerated files.
3. Optionally run `make docs-check` to confirm everything is now in sync.
4. **Stop before committing** (Golden Rule 1) — describe the changes and wait for the human.
