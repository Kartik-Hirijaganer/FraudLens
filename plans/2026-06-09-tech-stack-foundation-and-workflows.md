# Tech-Stack Foundation & Automation Workflows — FraudLens

> Status: **approved (review-revised), not yet implemented** (created 2026-06-09). Lands as a **single PR** (not split). Nothing here has been built yet.

## Context

FraudLens is a greenfield personal portfolio project — an **AML Fraud Investigation System** (XGBoost+SHAP scoring → LangChain+ChromaDB RAG over FinCEN/BSA regs → LangGraph orchestration → LLM-drafted SARs). The handoff doc (`docs/handoff/AML_Fraud_System_Handoff.docx`) defines the stack; the repo currently has only governance scaffolding (AGENTS.md/CLAUDE.md, `.claude/`, `plans/`, `docs/`, `DESIGN.md`, `.mcp.json`) and **no application code or tooling**.

This plan establishes the **development foundation, conventions, CI/CD, IaC scaffolding, and agent-facing automation workflows** so later feature work lands on rails that are green, documented, and governance-compliant from day one. It bakes in everything the existing **`drift-check`** skill audits (`/api/v1/` prefix for business APIs; camelCase API / snake_case Python; `{code,message,details,requestId}` error envelope; generated OpenAPI at `docs/reference/generated/api/` and ERD at `docs/reference/generated/erd/`; banned names; single Alembic head; `agency_id` on every tenant table) plus Aegis governance + Akeyless rules.

### Confirmed decisions
- **Cloud = Azure** (Container Apps + ACR + Blob) + **Vercel** (frontend) + **Supabase** (Postgres), per the handoff. **Phase 0 reconciles AGENTS.md** (before any app/IaC work) to: *"Deployment target is Azure; the AWS personal profile is local-only and not a project deploy target."* Azure account does not exist yet → deploy/IaC is **scaffolded and CI-validated but not applied** until the account + state backend exist.
- **Scope = Foundation + walking skeleton.** Tooling + conventions + CI/CD + IaC scaffolding + automation workflows, PLUS minimal infra/health endpoints, one Pydantic model, seed tests, a Dockerfile, and a sample React page — so every gate (lint/type/test/≥90% coverage/OpenAPI/header-check/secret-scan/docker-build) is demonstrably green. **Out of scope:** XGBoost/SHAP/RAG/LangGraph/SAR features, DB schema beyond a placeholder.
- **Frontend = TypeScript** (React + Vite + Tailwind), using the **`wise`** design system (`DESIGN.md`).
- **Python deps = `uv`** (reproducible `uv.lock`). **Node = npm** (reproducible `package-lock.json`, `npm ci`).
- **One PR**: the whole foundation lands together (review suggested splitting into 3 PRs — explicitly **not** doing that).

### Out of scope
ML/RAG/LLM feature code; real datasets; production secrets; applying Terraform; provisioning Azure/Vercel/Supabase.

---

## The parity architecture (the spine of this plan)

A **single source of truth** — a root `Makefile` — defines every check. The local pre-PR gate, GitHub Actions CI, and the deploy pre-gate all invoke the **identical** targets, so *"if CI passes, deployment won't fail"* is structural.

```
            scripts/ + Makefile   ← ONE definition of "green"
                    │
   ┌────────────────┼─────────────────────────┐
   ▼                ▼                           ▼
make pre-pr     ci.yml → _ci-reusable.yml   deploy-*.yml → _ci-reusable.yml
(local, writes  (PR/push: make ci +          (re-runs make ci + docker-build at
 docs then       docker-build,               the deployed SHA, THEN push/apply,
 runs make ci)   required status checks)     THEN post-deploy smoke test)
```

- `make ci` is **read-only** (lint/format-check/typecheck/test/coverage/header-check/secrets-scan/dup-check/docs-check). `make docker-build` (image build, no push) is a separate required check so the deploy image is proven in CI too.
- `make pre-pr` = `make fmt` → `make docs` → `make ci`. The **only writer** in the dev loop.
- Deploy parity uses a **reusable workflow** (`_ci-reusable.yml`) called by both `ci.yml` and the deploy workflows.

---

## Target monorepo structure

```
FraudLens/
├── DESIGN.md                     # ⭐ `wise` design system — read before writing any UI
├── Makefile                      # ⭐ single source of truth for all checks
├── pyproject.toml                # uv workspace root + ruff/mypy/pytest config
├── uv.lock  .python-version      # reproducible Python (3.11)
├── .pre-commit-config.yaml  .editorconfig  .gitleaks.toml
├── renovate.json                 # dependency updates (Renovate, not Dependabot)
├── config/{default,dev,prod}.yaml + README.md   # layered NON-secret config (Akeyless for secrets)
├── backend/                      # uv workspace member: FastAPI service
│   ├── pyproject.toml  Dockerfile  .dockerignore
│   └── src/fraudlens_backend/{main,settings}.py
│       ├── api/{deps,errors,ops}.py + api/v1/{router,health}.py   # /healthz,/readyz (ops) + /api/v1/* (business)
│       ├── models/common.py      # Pydantic (camelCase alias, Field descriptions)
│       └── middleware/logging.py # structured logs + PHI-scrub filter
├── packages/
│   ├── fraudlens-core/           # shared domain types + tenancy (agency_id) helpers
│   └── fraudlens-ml/             # heavy ML deps isolated; placeholder (features land later)
├── frontend/                     # React + TS + Vite + Tailwind (Vercel root)
│   ├── package.json  package-lock.json  eslint.config.js .prettierrc.json vitest.config.ts tailwind.config.ts tsconfig*.json
│   └── src/{main.tsx,App.tsx,components/ui/*,lib/{config,api}.ts,test/setup.ts}
├── infra/terraform/              # Azure: modules/{networking,identity,acr,blob,container_app} + environments/{dev,prod}
│   └── (per env) main.tf  backend.tf.template  providers.tf  variables.tf  *.tfvars  outputs.tf  .terraform.lock.hcl(tracked)
├── scripts/                      # check_headers.py, check_no_secrets.py, coverage.sh, deadcode.sh, update_docs.py (+lib/), run-code-review-graph-mcp.sh
├── tests/{unit,integration,fixtures}/   # synthetic data only (no PHI)
├── .github/workflows/{_ci-reusable,ci,deploy-backend,deploy-frontend,release,dependency-update}.yml
│   + pull_request_template.md  CODEOWNERS
├── .claude/commands/{pre-pr,docs,deadcode}.md   # thin wrappers over make targets
├── docs/
│   ├── architecture/ARCHITECTURE.md             # C4 + AUTOGEN markers (Mermaid)
│   ├── reference/generated/{api,erd}/           # OpenAPI + ERD (drift-check paths)
│   └── runbooks/{deploy-rollback,branch-protection}.md
├── CONTRIBUTING.md  CHANGELOG.md  cliff.toml  .mcp.json
└── (existing) AGENTS.md CLAUDE.md plans/ docs/handoff/ .gitignore .gitattributes LICENSE README.md
```

**Layering rule (ruff-enforced):** `fraudlens-core` depends on nothing internal; `fraudlens-ml` may use `core` but never `backend`; `backend` may use both.

**Endpoint contract:** ops/infra endpoints `/healthz` (liveness) and `/readyz` (readiness: DB/ChromaDB/Akeyless reachability) are **unprefixed**; **only business APIs carry `/api/v1/`** (e.g. `/api/v1/health` as the API-surface heartbeat). Smoke tests and platform probes use `/healthz` + `/readyz`.

---

## Rules to codify in AGENTS.md (seeded in Phase 0, formalized in Phase 9)

1. **Pydantic everywhere.** Every data boundary (request/response, domain, **config** via `pydantic-settings`) is a Pydantic v2 model; every field uses `Field(..., description=...)`. No bare dicts/dataclasses/TypedDict at boundaries.
2. **Top-of-file SUMMARY header on every source file** (`.py`/`.ts`/`.tsx`): sections `Summary` / `Key classes` / `Key functions` / `Notes`. Enforced by `scripts/check_headers.py` (CI-blocking).
3. **≥90% coverage**, both stacks; **new/changed functionality requires behavioral tests**. A changed-file coverage gate catches untested new files.
4. **No hardcoded values / no committed secrets.** Non-secret config → `config/*.yaml` + env (`pydantic-settings`); **secrets → Akeyless at runtime**, never `.env`/source/config. Enforcement: `gitleaks` scans the **whole repo** (primary) + ruff `PLR2004` + `scripts/check_no_secrets.py` (Akeyless/config-specific guard).
5. **No duplication.** Reuse shared logic from `fraudlens-core`; **APIs use query/path params instead of near-duplicate endpoints**; **no duplicate tables — extend/reuse**; banned names (`v2/new_/temp_/tmp_/old_/legacy_/copy_/_refactored`); `jscpd` + ruff `SIM`/`PL`.
6. **Docs stay fresh & visual.** `make docs` regenerates headers' inventory lines, OpenAPI, ERD, and the architecture doc's AUTOGEN sections; CI `docs-check` fails if stale. **All diagrams are Mermaid** (fenced ` ```mermaid ` blocks) — no binary image exports.
7. **Frontend linting:** ESLint flat config (typescript-eslint type-aware, react-hooks, jsx-a11y, `eslint-plugin-tailwindcss`) + Prettier; `tsc --noEmit`.
8. **Process & Git:** run `make pre-pr` before opening a PR; CI mirrors it; deploy re-runs `make ci`. **No commit/push to any branch — including bot/Renovate branches — without explicit human permission** (Golden Rule 1; no autonomous code commits).
9. **Release:** SemVer + Conventional Commits + tag-driven releases + auto CHANGELOG (`git-cliff`); a tag only ships from a CI-green commit.
10. **Cloud = Azure** (replaces the AWS-only rule); AWS personal profile is **local-only**, not a deploy target; secrets via Akeyless; Aegis governance unchanged.
11. **Frontend follows the `wise` design system.** Before writing/changing **any** UI, read [`DESIGN.md`](../DESIGN.md) and match its tokens, type scale, components, and Do's/Don'ts. Tokens only (Tailwind `theme`) — **no ad-hoc hex/px/off-scale radii/spacing**. Wise green `#9fe870` is the sole accent, CTA-only (never a success color); 24px (`xl`) radius on cards/buttons; display headlines weight 900; semantic positive/warning/negative palette for status. Re-theme only by re-running `npx getdesign@latest add wise`.

---

**Implementation phases** — each uses a `## Phase N` heading (so drift-check parses them); **all land in one PR**. Dependencies are listed after Phase 9.

## Phase 0 — Governance reconcile + convention seeding + gitignore prep
- (This plan is persisted here per Golden Rule 3.)
- **AGENTS.md cloud reconcile (do first):** state *"Deployment target is Azure (Container Apps/ACR/Blob + Vercel + Supabase); the AWS `personal-admin` profile is local-only and NOT a project deploy target."* Removes the current AWS-vs-Azure contradiction before any app/IaC work.
- **Seed minimum conventions into AGENTS.md now** (so later phases build on documented rules; Phase 9 expands): Python/TS style + tooling, Pydantic-boundary rule, SUMMARY headers, the Aegis API contract (`/api/v1` + camelCase + `{code,message,details,requestId}` envelope; `/healthz`+`/readyz` unprefixed), the `wise` design-system rule, the no-commit/push rule (incl. bot branches), and cloud = Azure.
- Extend `.gitignore`: `node_modules/`, `frontend/dist/`, `coverage/`, `htmlcov/`, `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `*.tfstate*`, `.terraform/`, `*.auto.tfvars`, `.vercel/`. **Keep tracked:** `uv.lock`, `frontend/package-lock.json`, `**/.terraform.lock.hcl`.
- Create `docs/reference/generated/{api,erd}/.gitkeep`.

## Phase 1 — Python workspace + tooling + layered config
- Root `pyproject.toml` (`[tool.uv.workspace] members=["backend","packages/*"]`), `.python-version=3.11`, dev-deps (ruff, mypy, pytest, pytest-cov, pytest-asyncio, httpx, interrogate, vulture), `uv.lock`.
- Tool config: **ruff** (`E,F,W,I,N,UP,B,A,C4,SIM,TID,PL,RUF`; flake8-tidy banned-api for layering; `PLR2004`), **mypy** (`strict`, `plugins=["pydantic.mypy"]`), **pytest** (`asyncio_mode=auto`, `--cov-fail-under=90`, branch coverage), **interrogate** (docstring ≥90).
- Member `pyproject.toml`s: `backend` (fastapi, uvicorn[standard], pydantic≥2.9, pydantic-settings, pyyaml + workspace deps), `fraudlens-core`, `fraudlens-ml` (heavy deps isolated; placeholder).
- `config/{default,dev,prod}.yaml` + `config/README.md` (precedence: default→env yaml→`FRAUDLENS_*` env; Akeyless boundary).

## Phase 2 — Backend walking skeleton (FastAPI) + Docker
- `settings.py` (`AppSettings(BaseSettings)` loads YAML + env; `extra="forbid"`; `environment: dev|prod`).
- `main.py` app factory; `api/ops.py` → **`GET /healthz`** (liveness) + **`GET /readyz`** (readiness probes); `api/v1/router.py` + `health.py` → **`GET /api/v1/health`** (business-surface heartbeat).
- `api/deps.py` — JWT dependency that **fails closed**: missing/invalid token or `agency_id`-claim mismatch → 401/403 by default. A dev bypass is honored **only** when `settings.environment != "prod"` AND an explicit `auth_dev_bypass` flag is set; a test asserts the bypass is inert under `environment="prod"` (cannot authenticate in prod mode).
- `api/errors.py` (Aegis envelope `{code,message,details,requestId}`, no stack traces); `models/common.py` (camelCase alias generator, `Field` descriptions, `TenantContext`/`ErrorResponse`); `middleware/logging.py` (structured + PHI-scrub).
- `fraudlens-core`: `types.py` (sample domain model), `tenancy.py` (`agency_id` helper).
- **`backend/Dockerfile`** (multi-stage, uv-based, non-root, runs uvicorn) + **`.dockerignore`**.
- Tests (`tests/unit`,`tests/integration`): `/healthz` + `/readyz` 200, `/api/v1/health` 200 + envelope, settings load, `agency_id` scoping 401/403, **fail-closed auth + prod-bypass-inert**, error envelope shape; ≥90% coverage. `tests/fixtures/README.md`: synthetic only.
- SUMMARY headers on all files.

## Phase 3 — Frontend scaffold (React + TS + Vite + Tailwind) + `wise` design system
- `package.json` scripts: `dev/build/lint/format:check/typecheck/test/coverage`; **commit `package-lock.json`** (CI uses `npm ci`).
- `eslint.config.js` (flat: js.recommended + tseslint.recommendedTypeChecked + react-hooks + jsx-a11y + eslint-config-prettier + `eslint-plugin-tailwindcss`), `.prettierrc.json` (printWidth 100), `vitest.config.ts` (coverage v8, thresholds 90/90/90/90), `tailwind.config.ts`+`postcss.config.js`, `tsconfig*.json`.
- **Design system = `wise`** — source of truth [`DESIGN.md`](../DESIGN.md). Translate tokens into `tailwind.config.ts theme`: `colors` (`primary #9fe870`, `ink #0e0f0c`, `canvas`/`canvas-soft`, positive/warning/negative families), `borderRadius` (`xl: 24px`), `spacing` (4px base `xxs`→`3xl`), `fontSize`+`lineHeight` (`display-mega`→`caption`), `fontFamily`. Self-host fonts via `@fontsource`: **Inter** (body) + **Manrope**/Inter-900 (display stand-in for Wise Sans).
- **Primitives** `src/components/ui/` (`Button` primary/secondary/tertiary, `Card`, `TextInput`, `Badge`) from the `DESIGN.md` specs; sample `App.tsx` uses them (sage canvas → white cards, lime CTA, 24px radius) at ≥90% coverage.
- `src/lib/config.ts` (`import.meta.env.VITE_*`; no hardcoded URLs), `src/lib/api.ts` (typed client, camelCase), `src/test/setup.ts` + seed tests.
- SUMMARY headers (TS block comment) on all files.

## Phase 4 — Single source of truth: Makefile + checker scripts + pre-commit
- `Makefile` targets (backend+frontend sub-targets): `install` (`uv sync` + `npm ci`), `fmt` (dev only), `lint`, `format-check`, `typecheck`, `test`, `coverage`, `header-check`, `secrets-scan`, `dup-check` (jscpd), `deadcode`, `docs`, `docs-check`, `openapi` (sync gate), `test-coverage-diff` (CI), **`docker-build`** (backend image, no push), **`ci`** (umbrella read-only), **`pre-pr`** (fmt→docs→ci), `dev`.
- **`secrets-scan` = `gitleaks detect` across the whole repo** (workflows, terraform, docs, tests, lockfiles, scripts) with `.gitleaks.toml` allowlists, **plus** `scripts/check_no_secrets.py` as a supplementary Akeyless/config guard (not the primary scanner).
- `scripts/check_headers.py` (stdlib; `ast` for Python docstring, top block for TS; require 4 sections in order; cross-check listed symbols vs AST-defined; skip `__init__.py`/`*.d.ts`/generated; non-zero on violation).
- `scripts/coverage.sh`, `scripts/deadcode.sh` (vulture + ruff F-codes + knip; allowlists; warn unless `DEADCODE_STRICT=1`).
- `.pre-commit-config.yaml` (fast read-only: ruff check, ruff format --check, gitleaks, `make docs-check`), `.editorconfig`.

## Phase 5 — Doc-update workflow + architecture doc
- `scripts/update_docs.py` (+ `scripts/lib/headers.py`, `scripts/lib/docs_arch.py`): **headers** (validate + sync machine-owned inventory lines; prose human-owned; idempotent), **openapi** (`app.openapi()` → `docs/reference/generated/api/openapi.json` + `endpoints.md`, `sort_keys`), **erd** (SQLAlchemy metadata → `docs/reference/generated/erd/erd.mmd`; tolerate "no models yet"), **arch** (regenerate only `<!-- AUTOGEN:* -->` regions). `--check` → `make docs-check` (regenerate to temp, diff, fail if dirty); inside `make ci`.
- `docs/architecture/ARCHITECTURE.md`: **Mermaid** C4 (context/containers/components), fraud-pipeline sequence, deployment topology (Azure+Vercel+Supabase), LLM primary/fallback, Aegis mapping = hand-authored; endpoints/ERD/module-map/config-keys = AUTOGEN.

## Phase 6 — CI (GitHub Actions)
- `_ci-reusable.yml` (`workflow_call`; setup-uv + setup-node; `npm ci`; matrix `backend`/`frontend` each running `make backend-ci`/`make frontend-ci`; **plus a `docker-build` job** that runs `make docker-build`; upload coverage; `fetch-depth: 0` for diff-coverage).
- `ci.yml` (on PR + push `main`; calls `_ci-reusable`; `concurrency` cancel-in-progress). Required status checks `ci / backend`, `ci / frontend`, `ci / docker-build`.
- `commitlint` (Conventional Commits), `CODEOWNERS`, `.github/release.yml`.

## Phase 6b — Dependency-update workflow (Renovate, PR-only)
Keep library packages current; updates arrive as **reviewable PRs**, never autonomous commits.
- **Renovate** (`renovate.json`) — covers uv (PEP 621), npm (maintains `package-lock.json`), Terraform, GitHub Actions, Docker; groups minor/patch, isolates majors, weekly cadence + immediate security bumps. (Renovate is the single dependency bot; no Dependabot.)
- **`make upgrade`** (local/manual): `uv lock --upgrade` + `npm --prefix frontend update` + `npm audit fix`, then `make pre-pr`.
- **`.github/workflows/dependency-update.yml`**: on each Renovate PR, runs the **same `make ci` + `make docker-build`**. **If checks fail, the PR is left red and flagged for a human** (or a human-invoked agent) to fix — **no autonomous code commits** (Golden Rule 1 / rule 8). Patch/minor PRs may automerge **only when fully green**; majors always need human review.
- *(Deferred option, not in this PR:* an auto-remediation bot would require a narrowly-scoped exception added to AGENTS.md first — explicit branch pattern, token scope, PR-only behavior, and audit trail.*)*

## Phase 7 — IaC (Terraform/Azure) — scaffold, CI-validated, not applied
- `infra/terraform/modules/{networking,identity,acr,blob,container_app}/` + `environments/{dev,prod}/` (`main.tf`, `providers.tf` `use_oidc=true`, `variables.tf`, non-secret `*.tfvars`, `outputs.tf`).
- **Remote state handling:** keep `backend.tf.template` (azurerm remote state w/ per-env key) **until the Azure state account exists**; do not commit an active `backend.tf` that would force backend init. CI validates with **`terraform init -backend=false && terraform validate`** + `terraform fmt -check` (no backend, no apply). **Track `.terraform.lock.hcl`** for provider reproducibility.
- `infra/terraform/README.md`: out-of-band state-account bootstrap, rename `backend.tf.template`→`backend.tf` at provisioning, apply order, GitHub-OIDC→Azure federation, Akeyless→`TF_VAR_*` mapping (app secrets stay runtime/Akeyless, never TF inputs).

## Phase 8 — Deploy + release (wired, inert until accounts exist)
- **Secrets posture:** no long-lived cloud secrets in GitHub. **Azure via GitHub→Azure OIDC** (federated, no stored client secret). **Vercel/Supabase creds fetched short-lived from Akeyless at job runtime** (Akeyless GitHub-OIDC/JWT auth), masked, never persisted.
- `deploy-backend.yml`: `on: workflow_run [ci] success on main` → `verify` (re-run `_ci-reusable` incl. `make ci` + `make docker-build` at `head_sha`) → `build-push` (Docker→ACR, tag = SHA + SemVer) → `terraform apply` (ACA staged revision at 0%) → `smoke` (**`/healthz` + `/readyz`** + `pytest -m smoke` against live URL) → promote traffic; `concurrency` no-cancel.
- `deploy-frontend.yml`: workflow_run-gated → `verify` → `vercel --prod` → smoke curl.
- `release.yml`: `on tag v*` → `verify` → `git-cliff` CHANGELOG → `softprops/action-gh-release`; stamps backend image tag + `VITE_APP_VERSION`.
- `cliff.toml`, `CHANGELOG.md`, `docs/runbooks/deploy-rollback.md`.

## Phase 9 — Process & rules (formalize)
- `.github/pull_request_template.md`: checklist mapped 1:1 to `make` targets + Aegis governance + a `drift-check` line.
- **AGENTS.md**: expand the Phase-0-seeded conventions into full Tech Stack / Code Conventions / Workflows sections (rules 1–11), confirm cloud = Azure.
- `CONTRIBUTING.md` + test templates (`tests/unit/_template_test.py`, `frontend/src/test/_template.test.tsx`).
- `.claude/commands/{pre-pr,docs,deadcode}.md` (run the make target, summarize, **stop before commit**).
- `docs/runbooks/branch-protection.md` (required checks = `ci / backend`, `ci / frontend`, `ci / docker-build`; applied manually via repo settings — Golden Rule 1).

**Dependencies:** Phase 0 seeds conventions for all; Phase 1 unblocks code; checker scripts (4) precede CI (6); the Dockerfile (2) precedes docker-build CI (6) and deploy (8); the health/ops routes (2) precede OpenAPI sync (5) and smoke (8); deploy/release (8) depend on CI (6) + IaC (7).

---

## Verification (end-to-end, after implementation)

- `make install && make ci` → green locally: lint, format-check, types, tests, ≥90% coverage (backend+frontend), header-check, **gitleaks secrets-scan**, dup-check, docs-check.
- `make docker-build` → backend image builds clean (no push).
- `make pre-pr` → formats, regenerates docs, runs `make ci`; `git status` clean afterward.
- Backend: `uv run uvicorn fraudlens_backend.main:app` → **`/healthz` + `/readyz` 200**, `/api/v1/health` 200 in the Aegis envelope; `/docs` serves Swagger; committed `openapi.json` matches `make openapi`. Auth fails closed; prod bypass test passes.
- Frontend: `npm --prefix frontend ci && npm --prefix frontend run coverage` ≥90%; sample page renders the `wise` theme.
- IaC: `terraform -chdir=infra/terraform/environments/dev init -backend=false && terraform … validate && terraform … fmt -check` pass (no apply, no backend init).
- `drift-check plans/2026-06-09-tech-stack-foundation-and-workflows.md all` → **Aligned** (`/api/v1` + ops endpoints, camelCase, error envelope, generated docs in sync, `agency_id`, no banned names, no dead code).
- Draft PR → `ci.yml` (`make ci` + `docker-build`) green; PR template renders.

## Open follow-ups (post-plan, need your action / accounts)
- **Provision Azure + Vercel + Supabase**, bootstrap the Terraform state account, rename `backend.tf.template`→`backend.tf`, set up GitHub→Azure OIDC + Akeyless secret paths → then flip deploy/release live and run the first `terraform apply`.
- Optional cosmetic: `drift-check/SKILL.md` examples reference `.agents/plans/` while the repo uses `plans/` (explicit paths work either way).
- The AWS `personal-admin` profile stays in `.claude/settings.local.json` for local use; not the project deploy target.

## Set up now (outside the phased build)
- **`.mcp.json`** wires **`code-review-graph`** (drift-check's preferred graph tools; via [`scripts/run-code-review-graph-mcp.sh`](../scripts/run-code-review-graph-mcp.sh), needs `uv tool install code-review-graph`) and **`context7`** (live library docs). Restart Claude Code to load them; set **`CONTEXT7_API_KEY`** in the env (Akeyless-preferred, or a gitignored `settings.local.json` `env` entry) — never commit it.
- **`DESIGN.md`** (root) — the **`wise`** design system; the frontend (Phase 3) and all future UI follow it (rule 11), with a pointer in AGENTS.md.

## Review feedback incorporated (P0–P2 + security)
- **P0** `### Phase N` → **`## Phase N`** (drift-check parsing); auto-remediation bot **removed** → Renovate **PR-only, no autonomous commits** (rule 8 / Phase 6b); cloud contradiction → **Phase 0 explicit AGENTS.md reconcile** (Azure deploy target; AWS local-only).
- **P1** health endpoints unified → **`/healthz` + `/readyz` (ops, unprefixed) + `/api/v1/health` (business)**; **Docker build contract** added (`backend/Dockerfile`, `.dockerignore`, `make docker-build`, CI `docker-build` check); Terraform → **`init -backend=false` + `backend.tf.template`**, track `.terraform.lock.hcl`; secrets scan → **repo-wide `gitleaks`** + custom guard; **npm `package-lock.json` + `npm ci`** required.
- **P2** **Renovate** chosen everywhere (`renovate.json`; Dependabot dropped); **minimum conventions seeded in Phase 0**, formalized in Phase 9.
- **Security** JWT **fails closed**; dev bypass gated to non-prod with a proving test; **no long-lived cloud secrets in GitHub** — Azure OIDC + short-lived Akeyless retrieval for Vercel/Supabase.
- **Not adopted:** the 3-PR split — this lands as **one PR** (per direction).
