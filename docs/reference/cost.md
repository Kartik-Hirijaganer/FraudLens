# Cost Reference

FraudLens is designed to stay inexpensive for local development and predictable in deployed
environments. This page tracks cost drivers, controls, and owner actions. Provider prices change;
verify exact numbers in Azure, Vercel, Supabase, Infisical, and LLM-provider calculators before a
real deployment.

## Cost Flow

```mermaid
flowchart LR
    txn["Transactions"] --> api["Container Apps API"]
    api --> db[("Supabase Postgres")]
    api --> rag[("ChromaDB index")]
    api --> llm["LLM calls for SAR drafting"]
    api --> blob["Blob artifacts"]
    api --> logs["Log Analytics"]
    fe["Vercel frontend"] --> api
```

## Drivers And Controls

| Driver | Cost Risk | Control |
| --- | --- | --- |
| Container Apps API | Idle or over-provisioned compute | Scale-to-zero for non-prod; keep CPU/memory right-sized. |
| Supabase Postgres | Storage growth, long retention | Synthetic data only; retention windows for jobs/inference; indexes scoped to query patterns. |
| Blob artifacts | SAR PDFs and model artifacts | Store generated artifacts only; keep local dev on filesystem backend. |
| Log Analytics | High-volume access/error logs | 30-day retention, daily cap, sampled app logs, no request bodies. |
| LLM SAR drafting | Token usage and retries | Mock mode for `make run`; OpenRouter only for `make run-live`; per-session/day budget guard; cache replay for identical drafts. |
| Vercel frontend | Build/runtime usage | Static Vite SPA; no server-rendered runtime in v1. |
| Dependency/security tooling | CI runtime | Consolidated `make` targets; avoid duplicated gates. |

## Local Development

Local demo cost is intended to be zero beyond the developer machine:

| Component | Local Mode |
| --- | --- |
| Database | Docker Postgres |
| Storage | `.local/artifacts` |
| Jobs | In-process/local backend |
| SAR drafting | Mock drafter, no provider key |
| RAG | Local ChromaDB persistent index |

## Runtime Budget Signals

| Signal | Source | Action |
| --- | --- | --- |
| SAR token/cost totals | `sar_drafts.token_usage`, `sar_drafts.cost_usd` | Alert on daily cost approaching the configured budget. |
| LLM call logs | `fraudlens.llm` structured events | Investigate fallback loops or unexpectedly large prompts. |
| Job counts | `job_executions` | Check repeated retrain/drift jobs before raising schedule frequency. |
| Log volume | Azure Monitor ingestion | Lower sampling or retention before increasing caps. |
| DB storage | Supabase dashboard | Archive synthetic runs or reduce retention windows. |

## Cost Guardrails

- Keep `FRAUDLENS_LLM_MODE=mock` for local demo and tests.
- Keep secrets and provider keys out of local `.env`; `make run-live` requires Infisical and
  `OPENROUTER_API_KEY`.
- Treat `system_config` budget keys as runtime tunables; audit changes via `/api/v1/config`.
- Prefer bounded batch sizes and CSV row caps over unbounded imports.
- Re-run `make deps-audit` after dependency upgrades; security fixes can change transitive cost or
  footprint.

## Pre-Deploy Checklist

| Check | Command Or Owner Action |
| --- | --- |
| API/resource sizing reviewed | Azure calculator using planned Container Apps CPU/memory and scale settings |
| Supabase tier selected | Supabase calculator or dashboard |
| Vercel project limits reviewed | Vercel dashboard |
| LLM provider budget set | Provider dashboard plus FraudLens runtime budget config |
| Log Analytics cap set | Terraform observability module before apply |
| Local checks green | `make pre-pr && make deps-audit` |
