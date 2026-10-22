# FraudLens — High-level architecture

This view describes the current working tree, including the CSV-screening changes on
`codex/csv-screening-actions`. It is a code-and-configuration view, not a verification of the
currently deployed revision. The Mermaid source is the repository's canonical diagram; the
editable draw.io presentation shows the same components and relationships using native shapes,
a wider layout, and a light canvas through the project's official draw.io MCP server.

```mermaid
---
title: FraudLens — High-level architecture
config:
  layout: elk
---
flowchart TB
    analyst(["AML analyst / reviewer<br/>Investigate, review and approve"])

    subgraph experience["EXPERIENCE AND IDENTITY"]
        direction LR
        web["Vercel · React / TypeScript SPA<br/>Transactions · Alerts · Investigations · SAR review<br/>One public hostname"]
        auth["Supabase Auth<br/>Sign-in and JWT issuance<br/>JWKS verification keys"]
    end

    subgraph azure["APPLICATION RUNTIME · AZURE CONTAINER APPS"]
        direction TB
        api["FastAPI gateway and business APIs<br/>JWT verification · RBAC · agency_id binding<br/>CSV admission · saved results · review actions"]
        execution["Investigation execution<br/>Inline runtime / durable worker mode<br/>Postgres leases · retries · fencing · event replay"]
        detection["LangGraph investigation pipeline<br/>Rules → XGBoost → SHAP<br/>Persist risk score, explanation and threshold alerts"]
        rag["Regulatory retrieval · ChromaDB<br/>FinCEN / BSA citations<br/>Local index baked into the image"]
        drafting["SAR drafting and deterministic quality gate<br/>Evidence grounding · schema / citation / fact checks<br/>Mock or live · optional multi-agent / model cascade"]
        jobs["Manual Container Apps Jobs<br/>Batch scoring · retraining<br/>Same backend image"]
    end

    subgraph state["DURABLE STATE AND ARTIFACTS"]
        direction LR
        db[("Supabase Postgres<br/>Transactions · import jobs · run queue / events<br/>Scores · alerts · SARs · model registry · audit")]
        blob[("Azure Blob Storage<br/>Model bundles<br/>Approved SAR PDFs")]
    end

    provider["Live model endpoints<br/>OpenRouter / configured vLLM route<br/>Authorized synthetic provenance only"]

    subgraph operations["DELIVERY, SECRETS AND OBSERVABILITY"]
        direction LR
        delivery["GitHub Actions → GHCR<br/>Quality gates · SHA-tagged backend image<br/>Human-approved deployment · Azure OIDC"]
        secrets["Infisical · prod only<br/>Secrets injected at process startup"]
        telemetry["Azure Monitor / Log Analytics<br/>Request IDs · PHI-free telemetry<br/>Liveness and readiness probes"]
    end

    analyst -->|"HTTPS"| web
    web -->|"Sign in / obtain JWT"| auth
    web -->|"Same-origin /api/* rewrite · JSON / SSE"| api
    api -.->|"Validate JWT using JWKS"| auth
    api -->|"Atomic CSV + transactions + pending runs; reads / review writes"| db
    api -->|"Explicit investigation / re-run"| execution
    execution -->|"Claim queued runs / persist progress"| db
    execution --> detection
    detection -->|"Persist before enrichment"| db
    detection -->|"Only when alert threshold is crossed"| rag
    rag -->|"Cited regulatory context"| drafting
    drafting -.->|"Live mode · egress and spend guards"| provider
    drafting -->|"Persist accepted draft or explicit failure outcome"| db
    api -->|"Approved PDF export"| blob
    api -->|"Authorized manual trigger"| jobs
    jobs -->|"Tenant-scoped batch work / model registry"| db
    jobs -->|"Read / write model artifacts"| blob
    delivery -.->|"Approved frontend deploy"| web
    delivery -.->|"Approved backend / job deployment"| azure
    secrets -.->|"External runtime injection"| azure
    azure -->|"Operational telemetry"| telemetry

    classDef person fill:#1e293b,stroke:#0f172a,stroke-width:2px,color:#f8fafc
    classDef experienceNode fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#172554
    classDef computeNode fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d
    classDef intelligenceNode fill:#f3e8ff,stroke:#9333ea,stroke-width:2px,color:#4c1d95
    classDef stateNode fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f
    classDef secretNode fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#7f1d1d
    classDef operationsNode fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e

    class analyst person
    class web,auth experienceNode
    class api,execution,detection,jobs computeNode
    class rag,drafting,provider intelligenceNode
    class db,blob stateNode
    class secrets secretNode
    class delivery,telemetry operationsNode

    style experience fill:#eff6ff,stroke:#93c5fd,color:#172554
    style azure fill:#f0fdf4,stroke:#86efac,color:#14532d
    style state fill:#fffbeb,stroke:#fcd34d,color:#78350f
    style operations fill:#f8fafc,stroke:#94a3b8,color:#334155
```

Blue identifies experience and identity; green identifies application compute; purple identifies
retrieval and drafting; amber identifies durable state; red identifies secret delivery. Solid
arrows show requests or data. Dashed arrows show trust, delivery, injection, or explicitly
conditional provider access; their labels distinguish these relationships.

- CSV acceptance atomically persists transactions and pending investigations. The durable queue
  is in Postgres. Inline mode supervises an upload worker within the API lifespan; worker mode
  uses a separate process. Container Apps Jobs are manual batch/retrain work, not this run queue.
- Below-threshold investigations stop after the persisted risk decision. Retrieval or drafting
  failure preserves the score and alert. Saved results return through the API; observation does
  not enqueue another run. Analysts must explicitly request a re-run.
- Live inference requires authorized persisted synthetic provenance. CSV/API-upload provenance
  is currently blocked from live drafting. Mock mode is keyless; live multi-agent routing also
  requires both process and tenant feature gates. A rejected draft is not served as accepted.
- Human review and approval remain explicit actions. A generated draft or approved PDF is not
  an automated filing to a regulator; no regulator integration is represented.
- All tenant business data and background work are scoped by verified `agency_id`. No real PHI
  is in scope. The application does not fetch secrets from Infisical itself.
- Production Terraform declares one warm API replica (`min_replicas = max_replicas = 1`).
  Kubernetes/AKS demonstrations and paid GPU experiments are ephemeral, not standing services
  in this topology. Optional vLLM routing does not imply a currently running GPU endpoint.

Evidence: [system architecture](ARCHITECTURE.md), [CSV-screening ADR](adr/ADR-031-atomic-upload-screening.md),
[durable execution ADR](adr/ADR-027-durable-investigation-execution.md),
[quality-gated drafting ADR](adr/ADR-030-quality-gated-sar-model-cascade.md),
[screening runbook](../runbooks/transaction-screening.md),
[pipeline wiring](../../backend/src/fraudlens_backend/pipeline_wiring.py),
[pipeline graph](../../packages/fraudlens-ml/src/fraudlens_ml/pipeline/graph.py),
[production configuration](../../config/prod.yaml),
[production Terraform](../../infra/terraform/environments/prod/main.tf),
[replica configuration](../../infra/terraform/environments/prod/prod.tfvars), and
[Vercel rewrites](../../frontend/vercel.json).
