# FraudLens — Agent & Contributor Guide

> Canonical operating guide for AI coding agents (**Claude Code**, **Codex**) and humans.
> Claude Code loads this via `@AGENTS.md` from `CLAUDE.md`; Codex loads `AGENTS.md` directly.
> Keep this file the single source of truth — don't fork the rules into tool-specific copies.

## Project

**FraudLens** is a **personal project** by
[`Kartik-Hirijaganer`](https://github.com/Kartik-Hirijaganer) exploring AML / fraud-detection
patterns in a multi-tenant, healthcare-adjacent context. It is run with production-grade
hygiene — **no real PHI**, no secrets in git, strict tenant isolation — even though it is a
personal repo. Handoff/context lives in
[`docs/handoff/AML_Fraud_System_Handoff.docx`](docs/handoff/AML_Fraud_System_Handoff.docx).

> This overview is intentionally a stub — expand it as the codebase lands.

## Golden Rules (non-negotiable)

1. **Never `git commit` or `git push` without explicit human permission.** Make and
   describe changes; wait for an explicit go-ahead before writing history or pushing.
2. **No secrets in `.env` or source.** All credentials come from **Infisical** (see
   [Secrets](#secrets)). `.env` is for non-secret local config only and stays gitignored.
3. **Plans live in [`plans/`](plans/)**, named `YYYY-MM-DD-<short-title>.md` — see
   [`plans/README.md`](plans/README.md).
4. **Documents live in [`docs/`](docs/)** per [`docs/README.md`](docs/README.md). Don't
   drop deliverables in the repo root.
5. **Hold the security governance below** on every change.

## Security & Governance (FraudLens)

Production-grade guardrails, applied even though this is a personal repo. Use **no real
PHI**; every change must preserve these invariants:

- **No PHI** in logs, error messages, URLs, or query-string parameters.
- **Tenant isolation:** every tenant-scoped DB query and background job is scoped by
  `agency_id`.
- **AuthZ:** validate the JWT `agency_id` claim against the requested resource — never
  trust a client-supplied tenant id.
- **Least privilege & auditability** for anything touching financial / AML data.

The `drift-check` skill audits implementations against these rules (see below).

## Secrets

- **Source of truth: Infisical Cloud** (`https://app.infisical.com`). Fetch secrets at
  runtime via the Infisical CLI/SDK/agent (e.g.
  `infisical run --env=prod --path=/backend -- <command>`) or through GitHub Actions OIDC
  machine identities; never hardcode or commit secrets.
- **Infisical has exactly one environment: `prod`.** Do not create or rely on any other
  Infisical environment. Local development may use non-secret local config, but any secret
  read must resolve from Infisical `prod`.
- **Do not use AWS SSM or Azure Key Vault as FraudLens app secret stores** unless a
  future plan explicitly changes the architecture. Azure OIDC is still used for Azure
  deploy authentication; it is not the app secrets source of truth.
- Do **not** put credentials in `.env`, code, config, or fixtures.
- `.env` (gitignored) may hold **non-secret** local config only.
- Reads of `.env*`, `*.pem`, `*.key`, and `secrets/` are denied to Claude Code via
  [`.claude/settings.json`](.claude/settings.json).

## Cloud & Deployment

**Deployment target is Azure** (Container Apps + ACR + Blob) + **Vercel** (frontend) +
**Supabase** (Postgres), per the handoff. This **replaces any AWS-as-cloud assumption**:

- The AWS **`personal-admin`** profile is **local-only** (CLI experiments, scratch
  storage) and is **NOT a project deploy target**. Nothing in FraudLens deploys to AWS.
- **Secrets** for deploy come from **Infisical** (short-lived, fetched at job/runtime via
  OIDC machine identities) and **GitHub→Azure OIDC** (federated, no stored client secret)
  — never long-lived cloud credentials in GitHub or the repo. See [Secrets](#secrets).
- Azure/Vercel/Supabase accounts **do not exist yet**: IaC and deploy workflows are
  **scaffolded and CI-validated but inert** (no `terraform apply`, no push) until the
  accounts and the Terraform state backend exist.
- FraudLens governance (above) is **unchanged** by the cloud choice.

## Accounts & Identity

**GitHub — personal.** FraudLens is a personal repo under
[`Kartik-Hirijaganer`](https://github.com/Kartik-Hirijaganer/FraudLens).

- The `origin` remote uses the `github-personal` SSH alias
  (`git@github-personal:Kartik-Hirijaganer/FraudLens.git`) so pushes authenticate as
  `Kartik-Hirijaganer` — **never** the work account `khirijaganer-premierhealthgroup`.
- Commits use the personal GitHub noreply email
  (`65550498+Kartik-Hirijaganer@users.noreply.github.com`); the work email is not used here.
- Golden Rule 1 still applies: no push without explicit permission.

**AWS — personal only, local use only (not a deploy target).** Use **only** the
`personal-admin` profile → account `970385384114` (SSO session `personal`, region
`us-east-1`). This profile is for local CLI work; the project deploys to **Azure**, not AWS
(see [Cloud & Deployment](#cloud--deployment)).

- `AWS_PROFILE=personal-admin` is set in
  [`.claude/settings.local.json`](.claude/settings.local.json) (gitignored).
- Re-authenticate with `aws sso login --sso-session personal` when the token expires.
- **Never** use the work profiles (`nightingale-*`, `bootstrap-admin`) for this repo.

## Frontend design system

The frontend follows the **`wise`** design system captured in [`DESIGN.md`](DESIGN.md)
(repo root, generated by `npx getdesign@latest add wise`). **Before writing or changing any
UI, read `DESIGN.md`** and match its tokens, type scale, components, and Do's/Don'ts.

- Style only via the design tokens (surfaced as Tailwind `theme` values) — **no ad-hoc hex
  colors, px sizes, or off-scale radii/spacing**.
- Wise green `#9fe870` is the sole brand accent, used **only** for the primary CTA (never as a
  success color); cards/buttons use the 24px (`xl`) radius; display headlines are weight 900;
  status uses the semantic positive / warning / negative palette.
- Re-theme only by intentionally re-running `npx getdesign@latest add wise`.

## Tech Stack & Code Conventions

The development foundation (tooling, CI/CD, IaC, automation) is defined in
[`plans/2026-06-09-tech-stack-foundation-and-workflows.md`](plans/2026-06-09-tech-stack-foundation-and-workflows.md).
The **root [`Makefile`](Makefile) is the single source of truth** for every check; the local
pre-PR gate, CI, and the deploy pre-gate all invoke the **identical** targets.

### Stack

- **Backend:** Python **3.11**, **`uv`** workspace (reproducible `uv.lock`). Members:
  `backend/` (FastAPI service), `packages/fraudlens-core` (shared domain types + tenancy),
  `packages/fraudlens-ml` (heavy ML deps, isolated). **Layering (ruff-enforced):**
  `fraudlens-core` depends on nothing internal; `fraudlens-ml` may use `core` but never
  `backend`; `backend` may use both.
- **Frontend:** **TypeScript** — React + Vite + Tailwind, **npm** (reproducible
  `package-lock.json`, `npm ci`); follows the `wise` design system above.
- **Cloud:** Azure + Vercel + Supabase (see [Cloud & Deployment](#cloud--deployment)).

### Code conventions (rules 1–11)

1. **Pydantic everywhere.** Every data boundary (request/response, domain, **config** via
   `pydantic-settings`) is a Pydantic v2 model; every field uses `Field(..., description=...)`.
   No bare dicts / dataclasses / `TypedDict` at boundaries.
2. **Top-of-file SUMMARY header on every source file** (`.py` / `.ts` / `.tsx`), sections in
   order: `Summary` / `Key classes` / `Key functions` / `Notes`. Enforced by
   `scripts/check_headers.py` (CI-blocking). `__init__.py`, `*.d.ts`, and generated files are
   exempt.
3. **≥90% coverage**, both stacks (branch coverage on Python); **new/changed functionality
   requires behavioral tests**. A changed-file coverage gate catches untested new files.
4. **No hardcoded values / no committed secrets.** Non-secret config → `config/*.yaml` + env
   (`pydantic-settings`, `FRAUDLENS_*`); **secrets → Infisical at runtime**, never
   `.env`/source/config/fixtures. Enforcement: repo-wide **`gitleaks`** (primary) + ruff
   `PLR2004` + `scripts/check_no_secrets.py` (Infisical/config guard).
5. **No duplication.** Reuse shared logic from `fraudlens-core`; APIs use query/path params
   instead of near-duplicate endpoints; no duplicate tables — extend/reuse. **Banned names:**
   `v2`, `new_`, `temp_`, `tmp_`, `old_`, `legacy_`, `copy_`, `_refactored`. Tooling:
   `jscpd` + ruff `SIM`/`PL`.
6. **Docs stay fresh & visual.** `make docs` regenerates header inventory lines, OpenAPI
   (`docs/reference/generated/api/`), ERD (`docs/reference/generated/erd/`), and the
   architecture doc's `<!-- AUTOGEN:* -->` regions; CI `docs-check` fails if stale. **All
   diagrams are Mermaid** (fenced ` ```mermaid ` blocks) — no binary image exports.
7. **Frontend linting:** ESLint flat config (typescript-eslint type-aware, react-hooks,
   jsx-a11y, `eslint-plugin-tailwindcss`) + Prettier; `tsc --noEmit`.
8. **Process & Git:** run `make pre-pr` before opening a PR; CI mirrors it; deploy re-runs
   `make ci`. **No commit/push to any branch — including bot/Renovate branches — without
   explicit human permission** (Golden Rule 1; no autonomous code commits).
9. **Release:** SemVer + Conventional Commits + tag-driven releases + auto CHANGELOG
   (`git-cliff`); a tag only ships from a CI-green commit.
10. **Cloud = Azure** (replaces the AWS-as-cloud assumption); the AWS personal profile is
    local-only, not a deploy target; secrets via Infisical; FraudLens governance unchanged.
11. **Frontend follows the `wise` design system** ([`DESIGN.md`](DESIGN.md)) — see
    [Frontend design system](#frontend-design-system) above.

### Endpoint & API contract (FraudLens)

- **Ops/infra endpoints are unprefixed:** `GET /healthz` (liveness) and `GET /readyz`
  (readiness: DB/ChromaDB/Infisical reachability). Smoke tests and platform probes use these.
- **Only business APIs carry `/api/v1/`** (e.g. `/api/v1/health` as the API-surface heartbeat).
- **Casing:** camelCase on the API surface, snake_case in Python internals (Pydantic alias
  generator bridges them).
- **Error envelope:** `{code, message, details, requestId}` — never raw stack traces or
  exception names.
- **AuthZ fails closed:** missing/invalid JWT or `agency_id`-claim mismatch → 401/403 by
  default; the dev bypass is honored **only** when `environment != "prod"` AND an explicit
  flag is set (proven inert in prod by test).

## Repository Layout

| Path | Purpose |
|------|---------|
| `AGENTS.md` / `CLAUDE.md` | Agent operating guides (this file is canonical) |
| `Makefile` | **Single source of truth** for every check (local = CI = deploy) |
| `pyproject.toml` / `uv.lock` / `.python-version` | uv workspace root + shared tool config (3.11) |
| `backend/` | FastAPI service (`src/fraudlens_backend/`) + `Dockerfile` |
| `packages/fraudlens-core` / `packages/fraudlens-ml` | Shared domain/tenancy + isolated ML deps |
| `frontend/` | React + TS + Vite + Tailwind (`wise` design system), Vercel root |
| `config/` | Layered **non-secret** config (`default/dev/prod.yaml`); secrets → Infisical |
| `infra/terraform/` | Azure IaC (modules + dev/prod envs); scaffolded, CI-validated, not applied |
| `scripts/` | Checkers (`check_headers`, `check_no_secrets`), `update_docs`, `coverage`/`deadcode` |
| `tests/` | Backend tests (`unit/`, `integration/`, `smoke/`, `fixtures/`) — synthetic data only |
| `.github/workflows/` | CI (`ci`, `_ci-reusable`), deploy (`deploy-*`), `release`, `dependency-update` |
| `.claude/settings.json` | Shared Claude Code permissions/policy |
| `.claude/settings.local.json` | Local, gitignored overrides (e.g. `AWS_PROFILE=personal-admin`) |
| `.claude/skills/` / `.claude/commands/` | Project skills (`drift-check`) + make-target wrappers |
| `plans/` | Dated implementation plans |
| `docs/` | Project documents (handoff, architecture, runbooks, reference) |

## Plans & drift-check

Write a plan in `plans/YYYY-MM-DD-<title>.md` before non-trivial work, using
`## Phase N` headings. After implementing, run the read-only **drift-check** audit:

```
drift-check plans/<file>.md phase=<N>     # one phase
drift-check plans/<file>.md all           # every phase
```

drift-check validates real repo state against the plan and the governance rules above.

## Dev Workflow

- Work on a branch; the default branch is `main` (currently on `initial-setup`).
- Keep changes scoped to one plan/phase where practical.
- Run drift-check before declaring a phase done.
- Commit/push only on explicit request (Golden Rule 1).
