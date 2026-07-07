# Local development — one-command demo

> Run the whole stack on your machine with **no cloud and no secrets**. This is the
> primary developer path (plan §3.4); it uses a docker Postgres, local file/job backends,
> and the **keyless mock SAR drafter**, so nothing reaches Azure, Vercel, Supabase, or any
> LLM provider.

## Prerequisites

- **Docker** (daemon running) — for the local Postgres.
- **uv** — Python toolchain / workspace runner (`uv sync --all-packages` once).
- **Node + npm** — for the Vite frontend (`npm --prefix frontend ci` once).

No API keys, Infisical login, or cloud accounts are required for the demo.

## Start it cleanly

Use this as the normal local application command:

```bash
make run
```

`make run` resets the FraudLens local Docker stack, drops the local Postgres volume, clears
generated local caches/state, frees FraudLens-owned dev listeners, re-applies migrations/seed/RAG
indexing, then starts the backend and frontend. It uses `POSTGRES_PORT=55432` by default to avoid
colliding with a local Postgres already bound to `5432`; override it if needed:

```bash
POSTGRES_PORT=5432 make run
```

## Start without reset

```bash
make local-demo
```

This lower-level command keeps existing local data/volumes and runs
[`scripts/local_demo.py`](../../scripts/local_demo.py), which:

1. checks the required tools are on `PATH`;
2. starts Postgres via [`docker-compose.local.yml`](../../docker-compose.local.yml) and waits
   for its healthcheck;
3. applies migrations + seed **when they exist** (they land in Phase 2 and are skipped, not
   failed, until then);
4. starts the gateway+services (`uvicorn`) and the frontend (Vite) with
   `FRAUDLENS_ENVIRONMENT=dev`, local storage/queue backends,
   `FRAUDLENS_LOCAL_JOB_EXECUTE_ON_SUBMIT=true`, and `FRAUDLENS_LLM_MODE=mock`;
5. waits for `GET /healthz`, prints the URL, and blocks until `Ctrl-C`, then shuts the child
   processes down cleanly.

Open the URL printed by the command. Preferred defaults are **http://localhost:5173** for the app
and **http://localhost:8000** for the gateway/API; if another project already owns those ports,
the script automatically selects free fallbacks and prints the actual URLs.

```mermaid
flowchart LR
    dev["make local-demo"] --> pg[("Postgres<br/>docker-compose")]
    dev --> be["gateway+services<br/>uvicorn default :8000"]
    dev --> fe["frontend<br/>Vite default :5173"]
    be -->|asyncpg| pg
    fe -->|REST + SSE| be
```

## Running live locally

Use this when you want local dev ergonomics (local filesystem storage and local job runner) while
exercising the real Supabase Auth, real Supabase Postgres, and live OpenRouter SAR drafting path.

### What

`make run-live` starts the backend and Vite frontend without Docker Postgres. It sets:

| Setting | Value |
| --- | --- |
| `FRAUDLENS_ENVIRONMENT` | `dev` |
| `FRAUDLENS_AUTH_DEV_BYPASS` | `false` |
| `FRAUDLENS_AUTH_JWKS_URL` | `<SUPABASE_PROJECT_URL>/auth/v1/.well-known/jwks.json` |
| `FRAUDLENS_AUTH_JWT_ISSUER` | `<SUPABASE_PROJECT_URL>/auth/v1` |
| `FRAUDLENS_AUTH_JWT_AUDIENCE` | `authenticated` |
| `FRAUDLENS_AUTH_ROLE_CLAIM` | `user_role` |
| `FRAUDLENS_LLM_MODE` | `live` |
| `FRAUDLENS_STORAGE_BACKEND` / `FRAUDLENS_QUEUE_BACKEND` | `local` |

### Why

This avoids `config/prod.yaml`'s Azure storage and queue settings while proving the live auth,
database, and LLM path before any Azure deployment.

### How

1. In Supabase, enable RSA JWT signing, enable email/password auth, and disable open signup.
2. Apply [`supabase/2026-07-06-auth-claims.sql`](../../supabase/2026-07-06-auth-claims.sql).
   The hook stamps top-level `agency_id` and `user_role` claims from `public.users`.
3. Store secrets in Infisical `prod`:

| Path | Key |
| --- | --- |
| `/backend` | `DATABASE_URL` using the direct/non-pooled connection for migrations, plus `SUPABASE_SERVICE_ROLE_KEY` |
| `/llm` | `OPENROUTER_API_KEY` |

4. Put public frontend values in `frontend/.env.local`:

```bash
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<publishable-anon-key>
VITE_API_BASE_URL=http://localhost:8000
```

5. Export the non-secret project URL for the live runner:

```bash
export SUPABASE_PROJECT_URL=https://<ref>.supabase.co
```

6. Run migrations and seed the bootstrap admin after creating that first admin in the Supabase
   dashboard:

```bash
infisical run --env=prod --path=/backend -- make db-migrate
FRAUDLENS_BOOTSTRAP_ADMIN_USER_ID=<auth-users-uuid> \
FRAUDLENS_BOOTSTRAP_ADMIN_EMAIL=<admin-email> \
infisical run --env=prod --path=/backend -- make db-seed
```

7. Build the local RAG index once, then start the live-local app:

```bash
make ingest-rag
make run-live
```

Real users sign in with Supabase email/password. The demo persona picker remains a Vite-dev
convenience and only works when the backend is running with the dev bypass (`make run`), not
`make run-live`.

## Companion commands

| Command | What it does |
|---|---|
| `make run` | Clean reset + reseed + boot the full stack. Default app command. |
| `make run-live` | Boot backend + frontend against real Supabase/Postgres + OpenRouter via Infisical. |
| `make rebuild` | Alias for `make run`. |
| `make local-demo` | Boot the full stack and print the URL (blocks until `Ctrl-C`). |
| `make local-demo-down` | Stop the stack (containers removed, data volume kept). |
| `make local-demo-reset` | Stop the stack, **drop the volume**, and remove `.local/`. |
| `make local-demo-smoke` | Headless gate: boot Postgres + backend, assert `/healthz` + `/readyz`, tear down. |

## Configuration & backends

- **Non-secret config** is layered `config/default.yaml → config/dev.yaml → FRAUDLENS_* env`
  (see [`docs/architecture/ARCHITECTURE.md`](../architecture/ARCHITECTURE.md) for the key
  table). Boot-critical edge config (CORS allowlist, rate limits, security headers, gateway
  routes) is loaded at startup and never depends on the database.
- **Backends** are config-driven (`FRAUDLENS_STORAGE_BACKEND`, `FRAUDLENS_QUEUE_BACKEND`):
  `local` uses the filesystem (`.local/artifacts`) and a local job runner. In `make run` /
  `make local-demo`, the Model Admin retrain button executes `uv run python scripts/retrain.py`
  synchronously via `FRAUDLENS_LOCAL_JOB_EXECUTE_ON_SUBMIT=true`, so local browser UAT creates a
  real candidate model instead of only acknowledging a job id. The cloud backends (Azure Blob /
  Container Apps Jobs) activate in production.
- **LLM** runs in `mock` mode locally (`FRAUDLENS_LLM_MODE=mock`) — a deterministic, keyless
  SAR drafter. Live providers are opt-in via Infisical in deployed environments.
- Copy [`.env.example`](../../.env.example) to `.env` to override the non-secret local
  values (ports, Postgres credentials). The demo also works with no `.env`.

## Data lifecycle (scaffolded; lands in later phases)

These targets wrap the canonical commands; the scripts they invoke arrive with their phases.

| Command | Purpose | Lands in |
|---|---|---|
| `make db-migrate` | Apply Alembic migrations | Phase 2 |
| `make db-seed` | Seed the demo dataset (dev/demo only) | Phase 2 |
| `make import-ieee` | Import the synthetic IEEE-CIS sample | Phase 3 |
| `make ingest-rag` | Build the FinCEN/BSA RAG index | Phase 6 |
| `make train-model` | Train + register an XGBoost model | Phase 5 |
| `make retrain` | Matured reviewed labels → gated candidate model | Phase 10 |

## Troubleshooting

- **Docker not running** → `make local-demo` exits with a clear "missing required tools" /
  daemon error; start Docker and retry.
- **Port already in use** → unset `BACKEND_PORT` / `FRONTEND_PORT` to let the script choose free
  fallbacks, or set `BACKEND_PORT` / `FRONTEND_PORT` / `POSTGRES_PORT` in `.env` explicitly.
- **`/readyz` reports the database as `down`** → Postgres is not up yet or `DATABASE_URL` is
  wrong; `make local-demo-down` then `make local-demo`. With no `DATABASE_URL` the database
  check reports `skipped` (the app still boots).
- **Reset everything** → `make local-demo-reset` (drops the volume and `.local/`).
