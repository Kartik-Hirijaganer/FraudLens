# FraudLens

FraudLens is a personal AML fraud-investigation system built with production-grade hygiene:
synthetic data only, fail-closed auth, tenant isolation by `agency_id`, masked-only PHI storage,
and a durable audit trail. It ingests transactions, runs rule/model investigations, surfaces
alerts for analyst review, drafts SAR narratives through a governed LLM seam, and manages model
lifecycle operations behind admin-only APIs.

## Stack

| Area | Implementation |
| --- | --- |
| Backend | FastAPI, Python 3.11, `uv` workspace |
| Domain packages | `fraudlens-core`, `fraudlens-ml`, `fraudlens-llm` |
| Frontend | React, TypeScript, Vite, Tailwind, Wise design system |
| Data | Supabase/Postgres target, SQLite in tests, ChromaDB persistent RAG index |
| Deploy target | Azure Container Apps + ACR + Blob, Vercel frontend, Infisical secrets |

## Local Development

```bash
uv sync --all-packages
npm --prefix frontend ci
make local-demo
```

Open `http://localhost:5173` for the frontend and `http://localhost:8000/docs` for the API docs.
The local demo uses docker Postgres, local artifact/job backends, and the keyless mock SAR drafter.
No cloud account or real secret is required.

## Source Of Truth

| Need | File |
| --- | --- |
| Agent/contributor rules | [AGENTS.md](AGENTS.md) |
| Current architecture | [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) |
| Local demo runbook | [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) |
| Security posture | [docs/runbooks/security.md](docs/runbooks/security.md) |
| PHI guardrails | [docs/runbooks/phi-guardrails.md](docs/runbooks/phi-guardrails.md) |
| API reference | [docs/reference/generated/api/openapi.json](docs/reference/generated/api/openapi.json) |
| Plans | [plans/](plans/) |

## Checks

The root [Makefile](Makefile) is the single source of truth for local, CI, and deploy gates.

```bash
make docs
make pre-pr
make deps-audit
```

Do not commit secrets or real PHI. Do not commit or push without explicit human approval.
