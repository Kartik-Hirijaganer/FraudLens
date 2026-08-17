<div align="center">

# FraudLens

**Explainable, tenant-safe AML investigations — from masked transaction ingest to risk scoring,
analyst review, grounded SAR drafts, and governed model operations.**

[![Run locally](https://img.shields.io/badge/demo-run%20locally-9fe870)](#quick-start)
[![CI](https://github.com/Kartik-Hirijaganer/FraudLens/actions/workflows/ci.yml/badge.svg)](https://github.com/Kartik-Hirijaganer/FraudLens/actions/workflows/ci.yml)
![Coverage](https://img.shields.io/badge/coverage-%E2%89%A590%25%20gated-success)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
<br/>
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Postgres](https://img.shields.io/badge/Postgres-16-4169E1?logo=postgresql&logoColor=white)
![XGBoost](https://img.shields.io/badge/ML-XGBoost%20%2B%20SHAP-orange)

**[Why](#why-fraudlens-exists)** · **[Capabilities](#what-it-does)** ·
**[Architecture](#how-it-works)** · **[Quick start](#quick-start)** ·
**[Engineering](#engineering-highlights)** · **[API](#api-surface)** ·
**[Docs](#documentation)**

> **Current status:** FraudLens runs locally. Azure, Vercel, and Supabase deployment resources are
> scaffolded and CI-validated, but no cloud environment is provisioned or hosting this application.

**Keywords:** AML · fraud detection · explainable AI · XGBoost · SHAP · LangGraph · regulatory RAG
· SAR drafting · multi-tenant SaaS · FastAPI · React · MLOps

</div>

---

## Why FraudLens exists

A fraud score by itself is not an investigation. An analyst also needs to know which signals fired,
why the model moved the score, what regulatory context applies, what action was taken, and whether
the entire decision can be reconstructed later. In a multi-tenant system, every one of those steps
must also preserve tenant isolation and prevent sensitive data from leaking through logs, prompts,
URLs, or errors.

FraudLens explores that complete decision path as a personal, production-hygiene project. It turns
public, synthetically generated AML transactions into explainable investigations and review-ready
alerts, while keeping analysts in control of alert decisions and SAR approval. The repository uses
no real PHI, stores only masked demo data, validates tenant identity from JWT claims, and keeps every
secret outside source control.

## What it does

- **Transaction ingest** — accepts single records, batches, or masked CSV uploads; list and search
  operations use tenant-scoped, keyset-paginated queries.
- **Hybrid risk scoring** — combines deterministic rules with a calibrated XGBoost model and assigns
  a risk band using the active model's operating points.
- **Explainable decisions** — records rule hits and additive SHAP feature contributions so analysts
  can see why a transaction moved toward or away from risk.
- **Thresholded investigation graph** — below-threshold runs stop after scoring; alerted runs continue
  through regulatory retrieval and SAR drafting, avoiding unnecessary LLM work.
- **Regulatory RAG** — retrieves versioned FinCEN/BSA context from ChromaDB, with a deterministic
  offline embedder for the default local demo and a guarded live embedding path as an opt-in.
- **Governed SAR drafting** — produces masked, cited draft narratives through a versioned prompt,
  strict output schema, citation grounding, budget guard, replay cache, and mock/live provider seam.
- **Analyst workflow** — exposes dashboards, transaction search, live investigation progress, alert
  review actions, SAR review, and role-aware navigation for analyst, reviewer, auditor, and admin
  responsibilities.
- **Human-gated MLOps** — supports retrain → candidate → shadow → approval → canary → active, plus
  rollback, per-tenant promotion gates, last-known-good model fallback, and advisory drift reports.
- **Auditable operations** — records request IDs, investigation transitions, model versions, prompt
  provenance, review decisions, and administrative lifecycle actions without logging PHI.
- **Tenant-isolation research** — includes a committed, redacted graph-feature study and interactive
  typology view that makes the performance-versus-isolation boundary explicit without querying live
  tenant data.

## How it works

### Architecture diagram

This is the **target deployment architecture** captured by the repository. It is scaffolded and
CI-validated, but it is **not currently deployed**. The main request and investigation path runs
top-to-bottom; dashed connections are runtime configuration or trust relationships.

```mermaid
flowchart TB
    user["AML analyst / reviewer"]

    subgraph experience["Experience and identity"]
        direction TB
        frontend["React + TypeScript SPA<br/>Vercel target"]
        auth["Supabase Auth<br/>email/password + JWT"]
    end

    subgraph azure["Azure runtime target — scaffolded, not deployed"]
        direction TB
        registry["GHCR / optional ACR<br/>versioned backend image"]
        gateway["Azure Container Apps<br/>FastAPI gateway + /api/v1"]
        pipeline["Investigation runtime<br/>rules + XGBoost + SHAP + LangGraph"]
        jobs["Container Apps Jobs<br/>batch score + retrain"]
        blob[("Azure Blob Storage<br/>model artifacts + SAR PDFs")]
        observe["Log Analytics + Application Insights"]
    end

    database[("Supabase Postgres<br/>agency_id-scoped state")]
    rag[("ChromaDB<br/>FinCEN / BSA index baked into image")]
    secrets[["Infisical prod<br/>runtime secrets"]]
    llm["Governed LLM provider<br/>OpenRouter live SAR path"]

    user --> frontend
    frontend -->|"sign in"| auth
    auth -->|"JWT"| frontend
    frontend -->|"HTTPS /api/v1"| gateway
    gateway -.->|"JWKS trust + agency_id validation"| auth
    registry -->|"pull image"| gateway
    registry -->|"same image"| jobs
    gateway --> pipeline
    pipeline -->|"tenant-scoped reads / writes"| database
    pipeline -->|"retrieve regulatory citations"| rag
    pipeline -->|"masked, grounded prompt"| llm
    gateway -->|"start admin jobs"| jobs
    jobs -->|"tenant-safe batch work"| database
    gateway -->|"artifacts + approved SAR PDFs"| blob
    jobs -->|"model bundles"| blob
    secrets -.->|"runtime injection"| gateway
    secrets -.->|"runtime injection"| jobs
    gateway --> observe
    jobs --> observe
```

The default developer stack maps those boundaries to Vite, local FastAPI, Docker Postgres, local
jobs/artifacts, the offline ChromaDB index, and the keyless mock SAR drafter. Infisical is used only
to inject the Kaggle token into the public IBM AML-Data fetch command; the token is removed before
database, backend, frontend, scoring, RAG, or SAR processes start.

### User flow — draft and submit a SAR for review

This is the implemented above-threshold happy path. The system generates the first grounded draft;
the analyst validates the evidence and sends it for internal review; the reviewer remains the human
approval gate. Regulatory filing is outside FraudLens.

```mermaid
flowchart TB
    subgraph analyst["Analyst"]
        direction TB
        signIn["Sign in"]
        transactions["Open Transactions"]
        select["Select or import a masked transaction"]
        start["Start investigation"]
        signIn --> transactions --> select --> start
    end

    subgraph system["FraudLens investigation"]
        direction TB
        authorize["Validate JWT, RBAC, and agency_id"]
        score["Run rules + active XGBoost model"]
        explain["Produce risk band + SHAP drivers"]
        threshold["Happy path: alert threshold crossed"]
        alert["Persist tenant-scoped alert"]
        retrieve["Retrieve FinCEN / BSA citations"]
        draft["Generate and persist masked, grounded SAR draft"]
        authorize --> score --> explain --> threshold --> alert --> retrieve --> draft
    end

    subgraph caseReview["Analyst case review"]
        direction TB
        evidence["Confirm Risk → Drivers → Citations → SAR draft"]
        regenerate["Regenerate the draft if needed, then confirm"]
        alertQueue["Open the generated alert in the Alerts queue"]
        submit["Click Send for review<br/>append action + audit event"]
        evidence --> regenerate --> alertQueue --> submit
    end

    subgraph reviewer["Reviewer"]
        direction TB
        openReview["Open the escalated alert"]
        assess["Review evidence, citations, narrative, and activity"]
        edit["Edit if needed<br/>persist a new masked draft version"]
        approve["Approve SAR"]
        complete["SAR approved<br/>PDF generation queued + audit trail updated"]
        openReview --> assess --> edit --> approve --> complete
    end

    start --> authorize
    draft --> evidence
    submit --> openReview
```

### Built with

| Layer | Stack |
| --- | --- |
| **Backend** | Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2 async · Alembic · structlog |
| **ML / investigation** | XGBoost · SHAP · scikit-learn · imbalanced-learn · LangGraph |
| **LLM / RAG** | Standalone governed LLM client · OpenRouter opt-in · versioned prompts · ChromaDB · deterministic hashing embeddings locally |
| **Frontend** | React 19 · TypeScript · Vite · Tailwind CSS · Wise design system · D3 force |
| **Data** | Docker Postgres 16 locally · Supabase Postgres as the unprovisioned cloud target · local artifact and queue backends |
| **Security** | Fail-closed JWT/AuthZ · `agency_id` tenant enforcement · RBAC · PHI masking · Infisical secrets · gitleaks |
| **Infra / CI** | Docker · Terraform · GitHub Actions · Azure Container Apps/ACR/Blob target · Vercel target |

## Quick start

### Prerequisites

- **Python 3.11** and [uv](https://docs.astral.sh/uv/)
- **Node.js 20+** and npm
- **Docker** with the daemon running
- **GNU Make** and Git
- **Infisical CLI** access to the FraudLens project, authenticated with `infisical login`
- Enough local space for the approximately 454 MB gitignored IBM AML-Data file plus Docker state

No Azure, Vercel, Supabase, or LLM-provider account is required for the default local demo.
Application/provider secrets never belong in `.env`; the optional `.env` file is for non-secret
local port and Docker overrides only.

### Install and run

```bash
git clone https://github.com/Kartik-Hirijaganer/FraudLens.git
cd FraudLens

infisical login       # one-time local authentication; the dataset fetch reads prod /ml
make install          # uv workspace sync + npm ci
make run              # clean local rebuild, ingest, score, then start API + frontend
```

`make run` is the normal, reproducible demo path. It:

1. drops the local Postgres volume and generated caches while preserving the downloaded IBM file;
2. verifies or fetches only `HI-Small_Trans.csv` from the public IBM AML-Data dataset;
3. removes the Kaggle token from the environment, starts Postgres, and applies migrations;
4. seeds foundation identity/config/rules and activates the best gates-passed local model bundle;
5. masks and ingests a bounded 1,600-row partition into the configured demo agency;
6. builds the offline regulatory index and batch-scores the rows through the production pipeline;
7. starts the FastAPI gateway and Vite frontend, then prints the actual local URLs.

Preferred URLs are:

| Surface | URL |
| --- | --- |
| Analyst application | [http://localhost:5173](http://localhost:5173) |
| API / gateway | [http://localhost:8000](http://localhost:8000) |
| Swagger UI | [http://localhost:8000/docs](http://localhost:8000/docs) |
| ReDoc | [http://localhost:8000/redoc](http://localhost:8000/redoc) |
| Liveness | [http://localhost:8000/healthz](http://localhost:8000/healthz) |
| Readiness | [http://localhost:8000/readyz](http://localhost:8000/readyz) |

If a default port is occupied, the runner selects a free fallback and prints it. Local mode enables
the development auth bypass only in the non-production environment, uses local storage/queue
backends, and uses the deterministic mock SAR drafter, so there is no provider cost.

> **Reset behavior:** `make run` intentionally rebuilds local database/generated state on each run.
> Use `make local-demo` when you want to keep the existing Docker volume and local state.

### Stop, preserve, or reset

```bash
# Stop the foreground API/frontend with Ctrl-C, then remove local containers but keep volumes:
make local-demo-down

# Start again without dropping the existing volume/state:
make local-demo

# Remove containers, volumes, generated state, AND the cached IBM download:
make local-demo-reset
```

`make local-demo-reset` is destructive only to gitignored local demo state. The next run must fetch
the IBM file again. For port overrides or troubleshooting, see the
[local development runbook](docs/runbooks/local-dev.md) and
[troubleshooting guide](docs/runbooks/troubleshooting.md).

### Optional live-service mode

`make run-live` keeps the frontend, backend, files, and job execution local but connects to real
Supabase Auth/Postgres and the guarded OpenRouter path. It is not the default demo and requires the
documented Supabase setup plus runtime values from Infisical `prod`; it never enables the auth
bypass. `make run-live-demo` additionally bootstraps the pinned portfolio story.

```bash
make ingest-rag-live
make run-live
```

Follow [Local development — Running live locally](docs/runbooks/local-dev.md#running-live-locally)
before using either live-service command.

## Engineering highlights

- **Tenant isolation is a boundary, not a filter.** Tenant-scoped tables and operations carry
  `agency_id`; authorization compares the verified JWT claim with the requested resource instead
  of trusting a client-supplied tenant ID. Offline graph research never becomes a cross-tenant
  serving dependency. → [Architecture](docs/architecture/ARCHITECTURE.md),
  [ADR-017](docs/architecture/adr/ADR-017-graph-feature-serving-boundary.md)
- **Explainability follows the exact served model.** Model bundles include feature metadata,
  calibration, a SHAP background, and checksums. Explanations are additive to the model margin, and
  the cache reloads when the active registry pointer changes. →
  [Model lifecycle](docs/runbooks/model-lifecycle.md)
- **Alert creation is earned by the pipeline.** Demo and production-shaped flows do not seed alerts
  directly: transactions run through scoring, threshold evaluation, persistence, retrieval, and
  drafting. Below-threshold runs short-circuit before RAG/LLM work. →
  [Architecture pipeline](docs/architecture/ARCHITECTURE.md#fraud-investigation-pipeline-target--opt-in-live-path)
- **SAR output is reviewable and reproducible.** Drafts retain prompt version/hash, grounded
  citations, safe structured output, token/cost metadata, and review state. Provider failure
  degrades to a completed investigation with score/evidence rather than losing the case. →
  [Architecture — SAR drafting](docs/architecture/ARCHITECTURE.md#sar-drafting--prompt-versioning)
- **Model promotion is quantitative and human-gated.** Candidates must clear global and per-tenant
  checks before shadow/canary/active transitions; canary evaluation can auto-abort, and rollback
  flips the pointer without a redeploy. → [Model lifecycle](docs/runbooks/model-lifecycle.md)
- **Local data provenance is explicit.** The default input is public, synthetically generated IBM
  AML-Data. Raw files remain gitignored; identifiers are masked before storage, and provenance is
  recorded. CI/tests remain reproducible on committed synthetic fixtures. →
  [ADR-018](docs/architecture/adr/ADR-018-portfolio-demo-data-provenance.md)
- **LLM access is policy-driven.** Provider/model selection, retention posture, data-class policy,
  retry/fallback eligibility, input masking, prompt-risk scans, output scans, and budgets are
  config-driven. The default local path is keyless. → [LLM configuration](config/README.md)
- **Local and CI gates share one contract.** The root Makefile drives lint, formatting, strict
  typing, branch coverage, changed-line coverage, tenancy checks, docs generation, duplication,
  secret scanning, dependency audits, Terraform validation, and container builds. →
  [Makefile](Makefile), [CI workflow](.github/workflows/ci.yml)

## Cloud deployment status

FraudLens is **not currently hosted**. The repository contains the intended deployment topology so
it can be reviewed and validated before any account is created, but workflows are inert until the
required accounts, state backend, identities, and explicit enablement exist.

| Surface | Intended target | Current status |
| --- | --- | --- |
| Backend API | Azure Container Apps + Azure Container Registry | Terraform/workflow scaffolded and validated; not applied or deployed |
| Frontend | Vercel | Build/deploy workflow scaffolded; no hosted project |
| Database | Supabase Postgres | Schema and live-local integration available; no hosted project provisioned for this application |
| Artifact storage | Azure Blob Storage | Terraform scaffolded; not provisioned |
| Secrets | Infisical Cloud | Active source of truth for local secret injection; future workloads use short-lived identity |

No workflow runs `terraform apply` or performs a cloud push until those prerequisites and explicit
deployment gates are configured. See the [Azure deployment runbook](docs/runbooks/azure-deploy.md)
and [deployment/rollback runbook](docs/runbooks/deploy-rollback.md) for the planned path.

## Project internals and reference

### Project structure

```text
.
├── backend/                    FastAPI service and deployable backend image
│   └── src/fraudlens_backend/ API, middleware, DB repositories, pipeline, jobs
├── packages/
│   ├── fraudlens-core/         Shared domain models and tenant enforcement
│   ├── fraudlens-llm/          Standalone governed provider client
│   └── fraudlens-ml/           Rules, scoring, SHAP, RAG, SAR protocols
├── frontend/                   React + TypeScript analyst/admin SPA
├── config/                     Layered non-secret app, LLM, and demo configuration
├── data/                       Committed synthetic fixtures and regulatory corpus
├── alembic/                    Postgres schema migrations
├── infra/terraform/            Inert Azure infrastructure scaffold
├── supabase/                   Auth-claim setup for optional live-local mode
├── scripts/                    Docs, ingest, training, demo, and governance tooling
├── tests/                      Unit, integration, security, smoke, and synthetic fixtures
├── docs/                       Architecture, runbooks, generated references
├── plans/                      Dated, phase-based implementation plans
├── DESIGN.md                   Canonical Wise frontend design system
├── Makefile                    Single source of truth for developer and CI commands
└── docker-compose.local.yml    Local Postgres 16 stack
```

### API surface

Operational probes are deliberately unprefixed. Business APIs use `/api/v1`, camelCase payloads,
and the error envelope `{code, message, details, requestId}`.

| Resource | Representative path | Operations |
| --- | --- | --- |
| Operations | `/healthz`, `/readyz` | Liveness and dependency readiness |
| Transactions | `/api/v1/transactions` | Ingest one/batch/CSV · list/search · read |
| Investigations | `/api/v1/investigations` | Start · snapshot · SSE progress · regenerate SAR |
| Alerts | `/api/v1/alerts` | List/read · analyst action · SAR review |
| Rules | `/api/v1/rules` | List · create · update · delete |
| Dashboard | `/api/v1/dashboard/metrics` | Tenant-scoped risk and workload metrics |
| Models | `/api/v1/model-versions` | Registry versions and active pointer |
| Model lifecycle | `/api/v1/training-runs`, `/api/v1/model-deployment` | Retrain · shadow · approve · canary · evaluate · rollback · drift |
| Identity/admin | `/api/v1/me`, `/api/v1/users`, `/api/v1/config` | Current principal · invite user · system configuration |

The committed machine-readable contract is
[`docs/reference/generated/api/openapi.json`](docs/reference/generated/api/openapi.json). When the
backend is running, use Swagger UI at `/docs` or ReDoc at `/redoc`.

### Developer commands

The root [Makefile](Makefile) is the single source of truth; CI invokes the same targets.

| Command | What it does |
| --- | --- |
| `make install` | Reproduce the Python `uv` workspace and frontend npm dependencies |
| `make run` | Reset generated local state, ingest/score IBM AML data, then run the full local app |
| `make local-demo` | Start the local demo without first deleting the existing volume/state |
| `make local-demo-down` | Stop and remove local containers while preserving volumes |
| `make local-demo-reset` | Remove local containers, volumes, caches, artifacts, and downloaded dataset |
| `make dev` | Print the standalone backend and frontend dev-server commands |
| `make test` | Run backend pytest and frontend Vitest suites |
| `make coverage` | Enforce at least 90% coverage for both stacks, with Python branch coverage |
| `make pre-pr` | Format, regenerate docs, and run the complete local CI gate |
| `make docs` / `make docs-check` | Regenerate or verify headers, OpenAPI, ERD, and architecture regions |
| `make deps-audit` | Run `pip-audit` and production npm dependency audit |
| `make train-model` | Train/register a reproducible synthetic XGBoost candidate without activating it |
| `make retrain` / `make drift-scan` | Run matured-label retraining or the advisory drift scan |
| `make docker-build` / `make tf-validate` | Validate the backend image and inert Terraform scaffold locally |

## Security and governance

- **No real PHI** in source, fixtures, logs, error messages, URLs, query strings, prompts, or demo
  data. Public source data is masked before persistence.
- **Tenant isolation** on every tenant-scoped query and background job through `agency_id`.
- **Fail-closed authorization** validates the JWT `agency_id` claim against the resource; the
  development bypass is explicitly enabled only outside production and is proven inert in prod.
- **Least privilege and auditability** for alert review, SAR decisions, configuration, training,
  promotion, canary, and rollback operations.
- **No secrets in `.env` or git.** Secrets resolve at runtime from the single Infisical `prod`
  environment; `.env` is limited to gitignored, non-secret local configuration.
- **Synthetic/public data only.** Do not introduce customer, patient, or production financial data
  into this repository or its demo workflows.

See [Security](docs/runbooks/security.md), [PHI guardrails](docs/runbooks/phi-guardrails.md), and
[Infisical secrets](docs/runbooks/infisical-secrets.md) before changing a data or trust boundary.

## Documentation

| Need | Source of truth |
| --- | --- |
| Architecture and implemented/target state | [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) |
| Local setup and live-local mode | [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) |
| Portfolio demo workflow | [docs/runbooks/portfolio-demo.md](docs/runbooks/portfolio-demo.md) |
| Model scoring, gates, canary, rollback, and drift | [docs/runbooks/model-lifecycle.md](docs/runbooks/model-lifecycle.md) |
| Security posture and PHI controls | [docs/runbooks/security.md](docs/runbooks/security.md) · [docs/runbooks/phi-guardrails.md](docs/runbooks/phi-guardrails.md) |
| Database schema and tenancy | [docs/reference/database.md](docs/reference/database.md) |
| Configuration and secrets boundary | [config/README.md](config/README.md) · [docs/reference/configuration.md](docs/reference/configuration.md) |
| Generated OpenAPI | [docs/reference/generated/api/openapi.json](docs/reference/generated/api/openapi.json) |
| Contributor/agent rules | [AGENTS.md](AGENTS.md) |
| Implementation plans | [plans/](plans/) |

## License

FraudLens is available under the [MIT License](LICENSE).

---

**Safety note:** FraudLens is an engineering and research project, not a production compliance
service or legal determination system. Human review remains required for alert disposition and SAR
decisions.
