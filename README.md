# FraudLens

FraudLens is a personal AML fraud-investigation system built with production-grade hygiene:
public/synthetic data only, fail-closed auth, tenant isolation by `agency_id`, masked-only PHI
storage, and a durable audit trail. It ingests transactions, runs rule/model investigations,
surfaces alerts for analyst review, drafts SAR narratives through a governed LLM seam, and manages
model lifecycle operations behind admin-only APIs.

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
make run
```

Open the URLs printed by the command (normally `http://localhost:5173` and an API fallback such as
`http://localhost:18000`). The local run resets Postgres, idempotently fetches the full public IBM
AML-Data `HI-Small_Trans.csv` through Infisical, ingests a bounded masked partition, and scores it
through the production investigation pipeline. The IBM download is synthetically generated public
data, not the old hand-written IEEE sample and not real customer data. Only the first fetch needs
the Infisical `/ml` Kaggle token; the running app uses local backends and the keyless mock SAR
drafter.

For the curated walkthrough — one demo tenant whose risk bands, alerts, and SAR states are produced
by the real pipeline and then asserted against
[config/portfolio-demo.yaml](config/portfolio-demo.yaml) — use `make run-live-demo` and verify it with
`make portfolio-demo-verify`. See [docs/runbooks/portfolio-demo.md](docs/runbooks/portfolio-demo.md).

## Source Of Truth

| Need | File |
| --- | --- |
| Agent/contributor rules | [AGENTS.md](AGENTS.md) |
| Current architecture | [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) |
| Local demo runbook | [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) |
| Portfolio demo story | [docs/runbooks/portfolio-demo.md](docs/runbooks/portfolio-demo.md) |
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
