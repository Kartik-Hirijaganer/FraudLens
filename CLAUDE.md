# CLAUDE.md

Project guidance for Claude Code. The canonical rules live in **[AGENTS.md](AGENTS.md)**
(shared with Codex) and are imported below — read them first.

@AGENTS.md

## Claude Code specifics

- **Permissions** are enforced by [`.claude/settings.json`](.claude/settings.json):
  `git commit` / `git push` / `git reset --hard` prompt for confirmation, and reads of
  secret files (`.env*`, `*.pem`, `*.key`, `secrets/`) are denied. This operationalizes
  Golden Rules 1 and 2.
- **Skills** ([`.claude/skills/`](.claude/skills/)):
  - `drift-check` — strict, read-only plan-vs-code audit. Invoke as
    `drift-check plans/<file>.md phase=<N>`.
- **Plan mode:** for multi-step work, draft the plan into `plans/YYYY-MM-DD-<title>.md`
  first, then implement and run drift-check.
- **Accounts:** personal GitHub (`Kartik-Hirijaganer`, via the `github-personal` SSH
  alias) and the `personal-admin` AWS profile (acct `970385384114`) — see
  [AGENTS.md → Accounts & Identity](AGENTS.md). Never the work account / `nightingale-*`.
- **MCP servers** ([`.mcp.json`](.mcp.json)): `code-review-graph` (drift-check's preferred
  graph tools — launched repo-scoped via
  [`scripts/run-code-review-graph-mcp.sh`](scripts/run-code-review-graph-mcp.sh); needs a
  one-time `uv tool install code-review-graph`) and `context7` (live library docs). Restart
  Claude Code to load them; approve the project servers when prompted.
- **Local overrides:** machine-specific settings live in `.claude/settings.local.json`
  (gitignored) — sets `AWS_PROFILE=personal-admin`. Put **`CONTEXT7_API_KEY`** here (in the
  `env` block) or export it from Akeyless; never commit it (Golden Rule 2).
