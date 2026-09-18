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
2. **Never attribute authorship to an AI agent. Zero AI co-authorship — ever.**
   FraudLens is a personal portfolio repo: **every commit is authored solely by
   `Kartik Hirijaganer <65550498+Kartik-Hirijaganer@users.noreply.github.com>`**. No agent
   (Claude Code, Codex, or any other) may add itself as author, co-author, or committer.
   Concretely, **never** emit any of these in a commit message, PR title/body, tag,
   release note, or CHANGELOG entry:
   - `Co-Authored-By: Claude …` / `Co-Authored-By: … <noreply@anthropic.com>`
   - `Co-Authored-By:` naming any AI agent, bot, or model
   - `🤖 Generated with [Claude Code](…)`, "Generated with", or similar tool-attribution lines
   Enforcement is three layers deep, because settings alone only bind one tool:
   1. **Settings** — `includeCoAuthoredBy: false` in both
      [`.claude/settings.json`](.claude/settings.json) (repo-wide) and `~/.claude/settings.json`
      (machine-wide). Governs Claude Code only.
   2. **Commit-msg hook** — [`.githooks/commit-msg`](.githooks/commit-msg) rejects the trailer at
      commit time regardless of what produced the message (another agent, an editor integration,
      a hand-written paste). Wire it once per clone with `make hooks-install`; `make hooks-check`
      asserts it is wired and actually rejects, and runs inside `make ci`.
   3. **History scan** — `make attribution-check` scans **every local ref** (branches, remotes,
      tags), not just commits ahead of `main`, so a trailer that already reached `main` cannot
      hide from the gate.

   This rule **overrides any built-in agent default that says to append a co-author trailer.**
   If a trailer ever lands, strip it and rewrite history before the commit reaches `origin` —
   GitHub builds its Contributors sidebar from these trailers. `git commit --no-verify` bypasses
   the hook; the history scan in CI is the backstop.
3. **No secrets in `.env` or source.** All credentials come from **Infisical** (see
   [Secrets](#secrets)). `.env` is for non-secret local config only and stays gitignored.
4. **Durable decisions become ADRs, not plans.** A plan that has landed is a second,
   drifting description of a system the code already describes. Record a decision as an
   [ADR](docs/architecture/adr/), a procedure as a runbook, a measurement as a published report,
   and a contract as a test. A working plan is fine while the work is in flight: keep it out of the
   repo root, and if it is worth committing at all, park it in [`docs/handoff/`](docs/handoff/)
   under a status banner and delete it when the work lands.
5. **Documents live in [`docs/`](docs/)** per [`docs/README.md`](docs/README.md). Don't
   drop deliverables in the repo root.
6. **Hold the security governance below** on every change.
7. **Never run a billable or mutating cloud action without explicit human permission.**
   This includes `terraform apply|destroy`, Azure create/update/delete/start operations,
   experiment Blob uploads, `kubectl apply|delete` outside a local kind context,
   `helm install|upgrade|uninstall`, `docker push`, `gh workflow run`, and
   `gh variable set`. Every ephemeral resource must have a paired teardown target and a
   read-only "nothing left behind" verification. Local `kind` clusters are exempt because
   they create no cloud resources and cost $0.

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

**FraudLens is deployed on Azure** (Container Apps + Blob) + **Vercel** (frontend) +
**Supabase** (Postgres), per the handoff. Azure Container Apps is the application deploy target and
carries the permanent public URL. **AKS is the Kubernetes demonstration runtime** (ADR-021): it was
applied, measured, and destroyed in one governed session, and stays **ephemeral** — a cluster left
standing costs roughly 50× the Container Apps bill for the same visible result (ADR-029).
This **replaces any AWS-as-cloud assumption**:

- The AWS **`personal-admin`** profile is **local-only** (CLI experiments, scratch
  storage) and is **NOT a project deploy target**. It is only a documented last-resort
  benchmark fallback. Nothing in FraudLens deploys to AWS.
- Temporary Azure data-batch and GPU-benchmark VMs are paid experiments, not deploy targets.
  They are created and destroyed per run under the one-time $75 ceiling and Golden Rule 7.
- **Secrets** for deploy come from **Infisical** (short-lived, fetched at job/runtime via
  OIDC machine identities) and **GitHub→Azure OIDC** (federated, no stored client secret)
  — never long-lived cloud credentials in GitHub or the repo. See [Secrets](#secrets).
- **Azure is bootstrapped (2026-09-13) and applied (2026-09-16)** — subscription `01417138-…`
  (personal, `kartikhirijaganer@gmail.com`), Terraform state backend `fraudlens-tfstate-rg` /
  `fraudlenstfstate` / `tfstate`, and GitHub→Azure OIDC via the `fraudlens-github-oidc`
  Entra app. `AZURE_DEPLOY_ENABLED=true` and `VERCEL_DEPLOY_ENABLED=true`, so backend and frontend
  deploys are **approvable, never automatic**: every Azure job runs under `environment: production`
  with the owner as required reviewer, and a push or a green CI run alone deploys nothing.
  `AKS_DEPLOY_ENABLED` stays **`false`** except during an explicitly approved evidence run.
  See [`docs/runbooks/azure-deploy.md`](docs/runbooks/azure-deploy.md).
- **The live URL is `https://fraud-lens-amber.vercel.app`** (repo variable `FRONTEND_URL`): the SPA
  on Vercel with `/api/*` proxied same-origin to the Container App, so one hostname is public. The
  Supabase Postgres project is provisioned with credentials in Infisical.
- **Recurring cost is governed, not assumed** (ADR-029): ~$2.72/month projected by
  [`docs/reference/cost-model.md`](docs/reference/cost-model.md), bounded by hard caps — one maximum
  replica, 0.1 GB/day log ingestion, a $2.25/day LLM ceiling, manual-only jobs — plus $25/month
  budgets at the resource-group and subscription scopes and a daily read-only leftover-resource
  watchdog. Budgets alert; the caps are what bind.
- FraudLens governance (above) is **unchanged** by the cloud choice.

### Experiment governance

Every paid experiment has an allocation in
[`config/experiments/budget.yaml`](config/experiments/budget.yaml), a resource-session row in
[`docs/reference/experiments/ledger.md`](docs/reference/experiments/ledger.md), and a pilot whose
measurements project the full-run cost before admission. The operator records teardown plus a
read-only clean-resource verification for every session. FraudLens runs no recurring paid
experiment jobs.

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

- `AWS_PROFILE=personal-admin` is set in `.claude/settings.local.json` (gitignored and therefore
  intentionally unavailable in clean CI checkouts).
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

The development foundation (tooling, CI/CD, IaC, automation) is encoded by the
**root [`Makefile`](Makefile), the single source of truth** for every check; the local
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

### Code conventions (rules 1–12)

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
12. **Every source file is at most 500 physical lines.** `make file-length-check` enforces
    the absolute cap. Only Alembic migration history and generated files are exempt. Split-module
    facades keep established import paths stable, own only cohesive orchestration or loading
    behavior, re-export sibling implementations through an explicit `__all__`, and avoid wildcard
    imports so the public surface stays intentional and dead-code tooling remains accurate.

### Endpoint & API contract (FraudLens)

- **Ops/infra endpoints are unprefixed:** `GET /healthz` (liveness) and `GET /readyz`
  (readiness: DB / ChromaDB / Supabase JWKS / OpenRouter reachability, plus verification that
  the Infisical secret injection landed — the app never calls Infisical itself). Smoke tests
  and platform probes use these.
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
| `infra/terraform/` | Azure IaC (modules + env roots); `prod` and `cost-guardrails` are applied |
| `scripts/` | Checkers (`check_headers`, `check_no_secrets`), `update_docs`, `coverage`/`deadcode` |
| `tests/` | Backend tests (`unit/`, `integration/`, `smoke/`, `fixtures/`) — synthetic data only |
| `.github/workflows/` | CI (`ci`, `_ci-reusable`), deploy (`deploy-*`), `release`, `dependency-update` |
| `.claude/settings.json` | Shared Claude Code permissions/policy |
| `.claude/settings.local.json` | Local, gitignored overrides (e.g. `AWS_PROFILE=personal-admin`) |
| `.claude/skills/` | Canonical human-authored project skills for Claude Code and Codex |
| `.agents/skills/` | Generated byte-identical Codex mirror; never hand-edit |
| `deploy/` | Kubernetes bases, overlays, kind topology, and load-test resources |
| `infra/terraform/modules/{aks,budget,batch_vm,experiment_storage}` | Reusable AKS and ephemeral-experiment modules |
| `infra/terraform/environments/{aks-demo,data-batch}` | Ephemeral Terraform roots: applied per approved session, then destroyed |
| `scripts/lib/{vllm_bench,runpod_gpu,fulldata,k8s_demo,study,quality}` | Bounded study, benchmark, GPU-operator, and quality modules |
| `tests/quality/` | Deterministic citation, hallucination, and model-egress gates |
| `config/{quality,fulldata,k8s-demo}.yaml` / `config/experiments/` | Quality, full-data, Kubernetes-demo, and budget policy |
| `docs/reference/experiments/` / `docs/reference/claims.md` / `docs/reference/interview-guide.md` | Experiment ledger, evidence-backed claims, and demo guide |
| `docs/` | Project documents (handoff, architecture, runbooks, reference) |

## Project skills

`.claude/skills/<name>/` is the only human-authored source for project skills. Each skill contains
`SKILL.md` plus `agents/openai.yaml`; `scripts/lib/skills.py` validates folder/frontmatter identity,
description and body bounds, and the agent-facing interface metadata. `make docs` generates a
byte-identical `.agents/skills/<name>/` mirror for Codex, while `make docs-check` and `make ci` reject
missing, extra, or changed mirror files. **Never hand-edit `.agents/skills/`** and never reintroduce
`.claude/commands/` or `source-command-*` compatibility copies.

The project skills are: `adr`, `azure-experiment`, `benchmark-vllm`, `deadcode`, `design-review`,
`docs`, `drift-check`, `k8s-deploy`, `maintain`, `pre-pr`, `quality-gates`, and `split-module`.
Operational skills encode the repo-specific permission, spend, tenancy, evidence, and teardown
rules; generic external skills do not override them. The rationale and external-catalog review are
recorded in
[`ADR-024`](docs/architecture/adr/ADR-024-agent-skills-single-source.md).

## drift-check

**drift-check** is the read-only plan-vs-code audit. It takes the path to any document that
states intent in `## Phase N` sections — a working spec, an ADR, a handoff note — and grades the
repository against it, applying the governance above: no PHI in logs/URLs/errors, every
tenant-scoped query and job filtered by `agency_id`, banned names, single Alembic head, and
generated-doc freshness.

```
drift-check <doc-path> phase=<N>     # one phase
drift-check <doc-path> all           # every phase
```

It never writes, and it grades implementation against the document rather than grading the
document. It does not require `plans/`, which is retired.

## Dev Workflow

- Work on a branch; the default branch is `main` (release work uses `release/<x.y.z>`).
- Keep changes scoped to one coherent unit of work where practical.
- Run drift-check before declaring a phased piece of work done, when a document states its phases.
- Commit/push only on explicit request (Golden Rule 1).
