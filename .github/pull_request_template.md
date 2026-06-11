<!-- Keep changes scoped to one plan/phase where practical. -->

## Summary

<!-- PR-SUMMARY:auto -->
_Auto-filled when the PR opens with the areas this change touches (backend, frontend, LLM, libraries, …). Add context under “What & why”._
<!-- /PR-SUMMARY:auto -->

## What & why

<!-- Summary of the change and the motivation. Link the plan/phase. -->

Plan: `plans/YYYY-MM-DD-<title>.md` (phase N)

## Pre-PR gate (`make pre-pr`)

- [ ] `make pre-pr` is green locally (= `make fmt` → `make docs` → `make ci`)
- [ ] `make lint` (ruff + ESLint) and `make format-check` (ruff-format + Prettier)
- [ ] `make typecheck` (mypy strict + `tsc --noEmit`)
- [ ] `make coverage` — **≥90%** both stacks; new/changed behavior has tests
- [ ] `make header-check` — every source file has the SUMMARY header
- [ ] `make secrets-scan` — gitleaks clean + no inline secrets in config
- [ ] `make dup-check` (jscpd) and `make docs-check` (headers + OpenAPI + ERD + arch in sync)
- [ ] `make docker-build` — backend image builds (if backend changed)

## Aegis governance

- [ ] **No PHI** in logs, errors, URLs, or query params
- [ ] Every tenant-scoped query/job is scoped by `agency_id`; JWT `agency_id` validated (fails closed)
- [ ] Error responses use the `{code, message, details, requestId}` envelope (no stack traces)
- [ ] Business APIs under `/api/v1/`; ops probes `/healthz`/`/readyz` unprefixed; camelCase API surface
- [ ] **No secrets** committed — credentials come from Infisical at runtime
- [ ] UI changes follow the `wise` design system ([`DESIGN.md`](../DESIGN.md)) — tokens only

## Drift-check

- [ ] Ran `drift-check plans/<file>.md <phase|all>` → **Aligned**

> Reminder (Golden Rule 1): do **not** commit/push without explicit human permission.
