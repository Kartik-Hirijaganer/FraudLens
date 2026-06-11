# FraudLens — Architecture

> **Diagrams are [Mermaid](https://mermaid.js.org/)** (text, diffable). The hand-authored
> sections capture intent; the `<!-- AUTOGEN:* -->` regions are regenerated from code by
> `make docs` and validated by CI (`make docs-check`) — **do not edit them by hand**.

FraudLens is an **AML fraud-investigation system**: transactions are risk-scored
(XGBoost + SHAP), enriched with regulatory context retrieved from FinCEN/BSA references
(LangChain + ChromaDB RAG), orchestrated through an investigation graph (LangGraph), and
summarized into draft SARs by an LLM. This document describes the **foundation/walking
skeleton**; ML/RAG/LLM features land in later plans, but the boundaries are drawn here.

## C4 — System Context

```mermaid
C4Context
    title System Context — FraudLens
    Person(analyst, "AML Analyst", "Reviews flagged activity, edits & files SARs")
    System(fraudlens, "FraudLens", "Risk scoring, regulatory RAG, SAR drafting (multi-tenant)")
    System_Ext(infisical, "Infisical", "Runtime secrets (no secrets in repo)")
    System_Ext(supabase, "Supabase", "Postgres (tenant-scoped data)")
    System_Ext(llm, "LLM provider", "SAR drafting (primary + fallback)")
    Rel(analyst, fraudlens, "Investigates, reviews drafts", "HTTPS")
    Rel(fraudlens, infisical, "Fetches secrets at runtime")
    Rel(fraudlens, supabase, "Reads/writes tenant data", "TLS")
    Rel(fraudlens, llm, "Drafts SAR narratives", "HTTPS")
```

## C4 — Containers

```mermaid
C4Container
    title Containers — FraudLens
    Person(analyst, "AML Analyst")
    Container(spa, "Frontend SPA", "React + TS + Vite (Vercel)", "wise design system")
    Container(api, "Backend API", "FastAPI on Azure Container Apps", "/api/v1 + /healthz,/readyz")
    ContainerDb(db, "Postgres", "Supabase", "agency_id-scoped tables")
    Container(vector, "Vector store", "ChromaDB", "FinCEN/BSA embeddings")
    System_Ext(infisical, "Infisical")
    Rel(analyst, spa, "Uses", "HTTPS")
    Rel(spa, api, "Calls", "HTTPS/JSON (camelCase)")
    Rel(api, db, "Queries (scoped by agency_id)")
    Rel(api, vector, "Retrieves regulatory context")
    Rel(api, infisical, "Fetches secrets at runtime")
```

## C4 — Components (Backend)

```mermaid
C4Component
    title Components — Backend API
    Container_Boundary(api, "FastAPI service") {
        Component(mw, "RequestContextMiddleware", "ASGI", "request id + PHI-safe structured logs")
        Component(ops, "Ops router", "/healthz, /readyz", "liveness + readiness probes")
        Component(v1, "API v1 router", "/api/v1/*", "business surface (camelCase)")
        Component(deps, "Auth deps", "fail-closed JWT", "agency_id claim validation")
        Component(errors, "Error handlers", "Aegis envelope", "{code,message,details,requestId}")
        Component(core, "fraudlens-core", "domain + tenancy", "require_agency_id")
    }
    Rel(v1, deps, "Depends on")
    Rel(deps, core, "Validates tenant via")
    Rel(v1, errors, "Errors rendered by")
```

## Fraud-investigation pipeline (target)

```mermaid
sequenceDiagram
    actor Analyst
    participant API as FastAPI
    participant Score as XGBoost+SHAP
    participant RAG as LangChain+ChromaDB
    participant Graph as LangGraph
    participant LLM as LLM (primary/fallback)
    Analyst->>API: Request investigation (agency_id from JWT)
    API->>Score: Score transaction (tenant-scoped)
    Score-->>API: risk_band + SHAP explanation
    API->>RAG: Retrieve FinCEN/BSA context
    RAG-->>API: Cited regulatory passages
    API->>Graph: Orchestrate investigation steps
    Graph->>LLM: Draft SAR narrative
    LLM-->>Graph: Draft (no PHI in prompts/logs)
    Graph-->>API: Draft SAR + citations
    API-->>Analyst: Review-ready draft
```

## Deployment topology

```mermaid
graph TD
    subgraph GitHub
        ci["GitHub Actions CI<br/>(make ci + docker-build)"]
    end
    subgraph Azure
        acr["ACR<br/>(backend image)"]
        aca["Container Apps<br/>(FastAPI)"]
        blob["Blob Storage"]
    end
    vercel["Vercel<br/>(frontend)"]
    supabase["Supabase<br/>(Postgres)"]
    infisical["Infisical<br/>(secrets)"]
    ci -->|OIDC, no stored secret| acr
    ci -->|terraform apply| aca
    acr --> aca
    aca --> blob
    aca -->|TLS| supabase
    aca -->|runtime fetch| infisical
    ci -->|vercel --prod| vercel
    vercel -->|/api/v1| aca
```

- **Azure via GitHub→Azure OIDC** (federated; no long-lived client secret in GitHub).
- **Vercel/Supabase credentials** are fetched **short-lived from Infisical at job/runtime**,
  masked, never persisted. Deploy is **inert** until the accounts + Terraform state exist.

## LLM catalog, routing, and guardrails

```mermaid
graph LR
    req["LLM request"] --> catalog["config/llm/catalog.yml<br/>capability + trust"]
    req --> providers["config/llm/providers.yml<br/>connection + governance"]
    catalog --> select["model selection<br/>kind/modality/intelligence/cost"]
    providers --> policy["data-class policy<br/>region/retention/ZDR/training"]
    select --> guardrails["input guardrails<br/>PHI masking + prompt-risk scan"]
    policy --> guardrails
    guardrails --> primary["primary provider adapter"]
    primary -->|retryable error| fallback["eligible fallback<br/>equal-or-stricter posture"]
    primary -->|ok| output["raw output scan<br/>phishing/policy"]
    fallback --> output
    output --> sanitize["safe_text sanitization<br/>raw output locked down"]
```

`fraudlens-llm` is a standalone async package. Model capability, pricing, and trust
metadata live in `config/llm/catalog.yml`; provider connection and governance posture
live in `config/llm/providers.yml`. API keys are env-var references only and resolve at
runtime from Infisical `/llm`.

Public calls enter through `LlmClient.generate()` or `LlmClient.embed()`. The client checks
provider data-class policy before any SDK call, masks PHI-like input locally, scans prompt
risk, prepends a fixed system policy, calls a private provider adapter, scans raw output
before sanitization, and returns only `safe_text` by default. Embeddings run policy and
masking before the provider call; vector storage and `agency_id` scoping remain backend
responsibilities.

Fallback is allowed only after retryable provider failures and only to providers that allow
the call's `DataClass` and maintain an equal-or-stricter governance posture. Fallback never
weakens region, retention, ZDR, or training-opt-out posture unless an explicit non-prod
override is set.

## Aegis governance mapping

| Aegis invariant | Enforced by |
| --- | --- |
| No PHI in logs/URLs/errors/query params | `middleware/logging.py` (PHI scrub, path-only logs); `api/errors.py` (no raw input/stack) |
| Tenant isolation (`agency_id` on every scoped op) | `fraudlens_core.require_agency_id`; `api/deps.py` (`enforce_tenant`) |
| AuthZ validates JWT `agency_id` vs resource | `api/deps.py` (`authenticate` fails closed; dev bypass inert in prod) |
| Aegis error envelope | `api/errors.py` → `{code, message, details, requestId}` |
| Secrets via Infisical, never repo | `config/*.yaml` (non-secret only); `gitleaks` + `scripts/check_no_secrets.py` |
| Generated docs stay in sync | `make docs` / `make docs-check` (this file's AUTOGEN regions, OpenAPI, ERD) |

## Module map

<!-- AUTOGEN:module-map -->
```mermaid
graph TD
    core["fraudlens-core<br/>(domain types, tenancy)"]
    llm["fraudlens-llm<br/>(catalog client, guardrails)"]
    ml["fraudlens-ml<br/>(scoring/RAG; placeholder)"]
    backend["fraudlens-backend<br/>(FastAPI service)"]
    ml --> core
    backend --> core
    backend -.may use.-> llm
    backend -.may use.-> ml
    ml -.may use.-> llm
```
<!-- /AUTOGEN:module-map -->

**Layering rule (ruff-enforced):** `fraudlens-core` imports nothing internal; `fraudlens-ml`
may import `core` but never `backend`; `backend` may import both.

## API endpoints

<!-- AUTOGEN:endpoints -->
| Method | Path | Handler |
| --- | --- | --- |
| GET | `/api/v1/agencies/{agency_id}` | `read_agency` |
| GET | `/api/v1/health` | `api_health` |
| GET | `/healthz` | `healthz` |
| GET | `/readyz` | `readyz` |
<!-- /AUTOGEN:endpoints -->

Ops probes (`/healthz`, `/readyz`) are **unprefixed**; business APIs carry **`/api/v1/`**.

## Configuration keys

Non-secret config only (layered `config/*.yaml` → `FRAUDLENS_*` env). Secrets come from Infisical.

<!-- AUTOGEN:config-keys -->
| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `app_name` | `str` | `'FraudLens'` | Human-readable service name. |
| `environment` | `Literal` | `'dev'` | Active deployment environment; gates the auth dev-bypass. |
| `log_level` | `str` | `'INFO'` | Python logging level name. |
| `api_v1_prefix` | `str` | `'/api/v1'` | Prefix for business APIs; ops endpoints stay unprefixed. |
| `request_id_header` | `str` | `'X-Request-Id'` | Response header carrying the per-request correlation id. |
| `auth_dev_bypass` | `bool` | `False` | Dev-only auth bypass; honored only when environment != 'prod'. |
<!-- /AUTOGEN:config-keys -->

## Data model (ERD)

<!-- AUTOGEN:erd -->
```mermaid
erDiagram
    %% No SQLAlchemy models defined yet (foundation walking skeleton).
    %% Regenerated by `make docs`; every tenant-scoped table will carry
    %% agency_id (Aegis multi-tenant isolation).
```
<!-- /AUTOGEN:erd -->
