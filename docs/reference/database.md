# Database reference

> FraudLens persistent schema (plan §9). **Engine:** Supabase Postgres in prod/local-demo;
> the test suite runs the same models/migration on SQLite. **ORM:** SQLAlchemy 2.0 async
> (asyncpg) + Alembic. The **entity-relationship diagram is generated from the models** by
> `make docs` → [`generated/erd/erd.mmd`](generated/erd/erd.mmd) and the
> [Architecture doc's ERD section](../architecture/ARCHITECTURE.md#data-model-erd). This page
> is the hand-maintained table reference; run `make docs` after model changes.

## Conventions

- **IDs** are UUID v4 (generated application-side, portable across Postgres/SQLite).
- **Money** is `NUMERIC(18,2)` with a separate ISO-4217 `currency`.
- **JSON blobs** use `JSONB` on Postgres (generic `JSON` on SQLite) via a portable variant.
- **Enums** are stored as their string values (`native_enum=False`) so migrations stay
  portable and adding a value never needs an `ALTER TYPE` (expand/contract, §9.3).
- **Timestamps:** `created_at` (+ `updated_at` on mutable tables) default to `now()`.
- **Constraint/index names** follow a fixed naming convention so Alembic diffs are stable.

## Tenancy model (the invariant)

Every **tenant-scoped** table carries an `agency_id` foreign key to `agencies`, indexed as
the leading column of an index or unique constraint. Most are NOT NULL; four are
**global-or-tenant** (`agency_id` nullable — NULL means a global/default row). The seven
**platform** tables carry no `agency_id`. The split is enforced in CI by
[`scripts/check_tenancy.py`](../../scripts/check_tenancy.py) (`make tenancy-check`), whose
allowlist mirrors `PLATFORM_TABLES` in the models package — so the check and schema cannot
drift.

| Category | Tables |
|---|---|
| **Platform** (no `agency_id`) | `agencies`, `training_datasets`, `model_training_runs`, `model_versions`, `model_evaluations`, `model_deployments`, `drift_reports` |
| **Tenant-scoped, NOT NULL** | `users`, `transactions`, `analysis_runs`, `analysis_results`, `rag_retrievals`, `alerts`, `alert_actions`, `sar_drafts`, `analysis_run_events`, `training_labels`, `model_inference_logs` |
| **Global-or-tenant** (nullable `agency_id`) | `aml_rules`, `system_config`, `job_executions`, `audit_logs` |

## Core tables (§9.1)

| Table | Purpose | Notes |
|---|---|---|
| `agencies` | Tenant root | `slug` UNIQUE; the only table with no `agency_id`. |
| `users` | Analyst/reviewer/admin per agency | `email` UNIQUE; idx `(agency_id, email)`. |
| `transactions` | Financial transactions (masked) | Account ids stored **masked** + `feature_hash`; no raw PHI (ADR-014). UNIQUE `(agency_id, external_id)`; `latest_run_id` is a denormalized pointer (no FK). |
| `aml_rules` | Deterministic AML rule definitions | `agency_id` NULL ⇒ global default rule. |
| `analysis_runs` | Persisted investigation runs | Status + per-step version provenance. |
| `analysis_results` | Immutable scoring/SHAP/rule-hit snapshot | One per run (UNIQUE `run_id`). |
| `rag_retrievals` | Regulatory citations retrieved for a run | One per run (UNIQUE `run_id`). |
| `analysis_run_events` | Ordered event log backing SSE replay (ADR-016) | UNIQUE `(run_id, seq)`; PHI-free payloads. |
| `alerts` | Alerts raised from a run | Idx `(agency_id, status)`, `(agency_id, assigned_to)`. |
| `alert_actions` | Append-only triage audit | `note` masked. |
| `sar_drafts` | Drafted SARs (always human-reviewed) | `content` masked; cost/tokens for audit. |
| `system_config` | Runtime/tenant tunables | UNIQUE `(agency_id, key)`; boot config stays in YAML/env (§12.3). |
| `job_executions` | Background-job audit (incl. seed) | |
| `audit_logs` | Append-only, PHI-free audit | Column `metadata` (ORM attr `meta`); idx `(agency_id, created_at)`, `(resource_type, resource_id)`. |

## Model-lifecycle tables (§9.2)

| Table | Purpose | Notes |
|---|---|---|
| `training_labels` | Labels from matured reviewed decisions (tenant) | |
| `training_datasets` | Immutable, content-hashed dataset manifest | Feature names only — no PHI / no `agency_id` feature (ADR-015). |
| `model_training_runs` | One training run | trigger/params/metrics/artifact. |
| `model_versions` | The model registry | `version_label` UNIQUE; lifecycle status. |
| `model_evaluations` | Candidate-vs-baseline eval with pass/fail gate | Overall + per-tenant-slice metrics (ADR-015). |
| `model_deployments` | Single active/canary pointer (+ previous) | In-place update ⇒ reload, no redeploy (§10.5). |
| `model_inference_logs` | Hash-only inference record (tenant) | `feature_hash` only — never PHI. |
| `drift_reports` | Advisory drift reports | |

## Migrations & seed

- **Migrations:** Alembic, hand-reviewed, **expand/contract**. The initial migration
  [`alembic/versions/0001_initial_schema.py`](../../alembic/versions/0001_initial_schema.py)
  creates every table/index/FK. Apply with `make db-migrate` (`alembic upgrade head`);
  up/down are tested on a temp DB. There is exactly one Alembic head.
- **Seed (`make db-seed`, dev/demo only, idempotent):** the demo agency + users, default
  global `system_config`, and the active fixture model; recorded in `job_executions`.
  Refuses to run when `environment == "prod"`. Later phases extend the seed.
