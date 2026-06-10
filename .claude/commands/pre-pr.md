---
description: Run the pre-PR gate (make pre-pr) and summarize; never commit or push.
---

Run the full pre-PR gate and report the outcome.

1. Run `make pre-pr` (this is `make fmt` → `make docs` → `make ci`).
2. If any target fails, show that target's output and stop — do not try to "fix and commit".
3. If it passes, run `git status --short` and summarize what changed (formatting,
   regenerated headers/OpenAPI/ERD/architecture).
4. **Stop before committing.** Per Golden Rule 1, never `git commit` or `git push`
   without explicit human permission — including on bot/Renovate branches.
