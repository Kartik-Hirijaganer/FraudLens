# Initial Repo Setup — Claude Code, Codex & Conventions

## Context

Bootstrap FraudLens for agent-assisted development: configure Claude Code and Codex,
encode operating rules, and scaffold `plans/` + `docs/`. Requested 2026-06-09.

## Phase 1 — Structure & skill install

- [x] Create `.claude/`, `plans/`, `docs/{handoff,architecture,runbooks,reference}/`
- [x] Install `drift-check` skill at project level (`.claude/skills/drift-check/`)
- [x] Move `AML_Fraud_System_Handoff.docx` into `docs/handoff/`

## Phase 2 — Agent configuration

- [x] `AGENTS.md` — canonical guide (5 golden rules + Aegis/PHI governance + Akeyless)
- [x] `CLAUDE.md` — imports `AGENTS.md`, adds Claude Code specifics
- [x] `.claude/settings.json` — ask before commit/push/reset, deny secret-file reads

## Phase 3 — Conventions & hygiene

- [x] `plans/README.md` — dated naming + drift-check workflow
- [x] `docs/README.md` — document scaffolding
- [x] `.gitattributes` — Office/PDF docs tracked as binary
- [x] Extend `.gitignore` — agent tooling + secret material (skills/settings stay tracked)

## Phase 4 — Accounts & identity

- [x] AWS confirmed: `personal-admin` → account `970385384114`; `AWS_PROFILE` set in `.claude/settings.local.json`
- [x] GitHub confirmed personal (`Kartik-Hirijaganer`): remote → `github-personal` alias, commit email → personal noreply (repo-scoped)
- [x] Encode **Accounts & Identity** rules in `AGENTS.md`; reword project framing to personal-project

## Notes / Follow-ups

- Wire Akeyless retrieval when application code lands; keep creds out of `.env`.
- Optional: add the `code-review-graph` MCP server (preferred by drift-check) to
  `.claude/settings.local.json` — machine-specific, not committed.
- Nothing committed — awaiting explicit permission (Golden Rule 1).
