# Observability & audit

> Runbook for the FraudLens observability layer: structured logging, what is (and is never)
> logged, correlation, the durable audit trail, security/LLM-cost events, the dashboard-metrics
> endpoint, retention, and Azure Monitor alerts. Implements plan §11 (Logging, Observability &
> Retention) and the Phase 12 work (§16). Pairs with [phi-guardrails.md](phi-guardrails.md)
> (PHI never reaches a log/prompt) and the governance rules in [AGENTS.md](../../AGENTS.md).

## 1. Two record streams: app logs vs. the audit trail

FraudLens keeps two deliberately separate streams (plan §11.6):

| Stream | Where | Retention | Purpose |
|--------|-------|-----------|---------|
| **Application logs** | stdout JSON → Azure Monitor → Log Analytics | **30d** (capped + sampled) | ephemeral operational telemetry (access, security, LLM cost, errors) |
| **Audit logs** | Postgres `audit_logs` | **2y** | durable, immutable, tenant-scoped "who did what to which resource, when, under which requestId" |

App logs are for operating the service; the audit trail is the compliance record. **PHI-access /
masking events are audit events** (plan §11.6/§11.7), not just app logs.

## 2. Structured logging ([`middleware/logging.py`](../../backend/src/fraudlens_backend/middleware/logging.py))

`structlog` over the stdlib backbone emits **JSON in prod/staging** and a console renderer in dev.
The processor pipeline is: merge contextvars (`requestId`/`agencyId`/`userId`) → add level → ISO
timestamp → exception formatting → **PHI/secret redaction** → render. The redaction step runs LAST
so even a rendered traceback is scrubbed.

- **Redaction** masks denylisted keys (`token`, `authorization`, `password`, `secret`, `*_key`,
  `database_url`, `origin_account`, `dest_account`) and scrubs PHI-shaped substrings (SSN, email,
  13–19 digit card/account numbers) from every value and message.
- The `token` rule masks **credential** tokens (`token`, `access_token`, …) but **not** LLM token
  **counts** (`input_tokens`/`output_tokens`/`total_tokens`), which §11.3 logs for cost dashboards.
- **`bind_identity(agency_id, user_id)`** binds the verified tenant/user onto the contextvars after
  a route resolves its tenant ([`api/deps.py`](../../backend/src/fraudlens_backend/api/deps.py)),
  so the gateway access-log line and every record in the request are correlated to the tenant.

### What to log / never log (plan §11.3)

- **Log:** access (`method, path, status, durationMs, requestId, agencyId, userId, route`), domain
  events, **security events** (`auth_fail`, `tenant_mismatch`, `rate_limited`, `guardrail_block`),
  LLM calls (`model, promptVersion, promptHash, tokens, costUsd, fallbackCount, cached`), job
  executions, server-side errors.
- **Never log:** PHI/PII, secrets, tokens/JWTs, credentials, connection strings, raw request
  bodies, **prompt/response content**, full feature payloads. (Two independent nets enforce this:
  call sites pass only safe fields, and the redaction processor scrubs anything that slips through.)

## 3. Telemetry events ([`telemetry.py`](../../backend/src/fraudlens_backend/telemetry.py))

`telemetry.py` is the single seam for the PHI-free structured events:

- **`log_security_event(event, **fields)`** — emits a `WARNING` security event (`fraudlens.security`
  logger). Emitted from `authenticate`/`enforce_tenant` on `auth_fail`/`tenant_mismatch`, and by the
  gateway on `rate_limited`. Durable security facts are *also* written to `audit_logs` where a
  tenant-scoped session is available (plan §11.7).
- **`log_llm_call(...)`** — emits an `llm.call` cost/usage event (`fraudlens.llm` logger) from the
  SAR pipeline step ([`pipeline_wiring.py`](../../backend/src/fraudlens_backend/pipeline_wiring.py)):
  model + prompt provenance (version + **hash**, never the text), token counts, USD cost, fallback
  hops, cache hits. Never prompt/response content — the full masked SAR lives in `sar_drafts`.
- **`init_telemetry(settings)`** — config-gated OpenTelemetry → Azure Monitor exporter, **OFF by
  default** (`FRAUDLENS_TELEMETRY_ENABLED=false`). In v1 it is a no-op: Container Apps streams the
  stdout JSON to Log Analytics regardless, so the structured logs *are* the telemetry; the live
  exporter is wired with the Azure deploy (Phase 14), like the scaffolded-but-inert Terraform.

## 4. Correlation IDs (plan §11.4)

The **gateway** issues/accepts `X-Request-Id`, binds it to the structlog contextvar, returns it in
the response header, and (at the service split) propagates it to internal services via the signed
identity header. The SSE stream and the error envelope (`{code, message, details, requestId}`) both
carry it, and client errors reference it — end-to-end correlation across gateway → services → jobs.

## 5. The audit trail ([`audit_logs`](../../backend/src/fraudlens_backend/db/models/ops.py))

Every **mutating** business endpoint and every **model deployment** writes a PHI-free `audit_logs`
row through the shared `audit_writer` seam
([`db/repositories/audit.py`](../../backend/src/fraudlens_backend/db/repositories/audit.py)):

| Surface | Actions |
|---------|---------|
| Transactions | `transaction.ingest`, `transaction.batch_ingest`, `transaction.csv_import` |
| Investigations | `investigation.start` |
| Rules | `rule.create`, `rule.update`, `rule.delete` |
| Alerts / review | `alert.*`, `sar.*` |
| Model lifecycle | `model.shadow`, `model.approve`, `model.canary`, `model.activate`, `model.rollback`, `model.canary_auto_abort`, `model.retrain_triggered` |
| PHI | `phi_access`, `phi_mask` |

Each row records actor, action, resource type/id, **scrubbed metadata** (ids/counts/enums only —
never a note, account, or value), and the request id. Rows are append-only and tenant-scoped.

## 6. Dashboard metrics ([`GET /api/v1/dashboard/metrics`](../../backend/src/fraudlens_backend/api/v1/dashboard.py))

The analyst dashboard reads one tenant-scoped, PHI-free aggregate: alert/transaction/run/SAR counts,
SAR **LLM cost** (today + all-time USD, from `sar_drafts.cost_usd`), and **model health** (active +
canary version labels + percent, tenant inference count, latest advisory drift severity). Tenant
counts are filtered by the verified `agency_id`; the model-health signals are the shared global
registry pointer (models are global, ADR-015). The frontend `Dashboard` page renders from it.

## 7. Retention (plan §11.5)

| Log type | Store | Retention | Why |
|----------|-------|-----------|-----|
| App / access logs | Log Analytics | **30d** (capped + sampled) | cost control |
| Traces / APM | Application Insights | 30d | cost control |
| **Audit logs** | Postgres `audit_logs` | **2y** | compliance, durable, queryable |
| Security events | Log Analytics + `audit_logs` | 90d / 2y | alerting + durable record |
| LLM cost/usage | `sar_drafts` + Log Analytics | per-row / 90d | cost dashboards |
| `model_inference_logs` | Postgres | 90d | drift/shadow, hash-only |
| `job_executions` | Postgres | 90d | ops |

The Log Analytics **30-day daily cap** keeps ingestion near-free; tune the cap + sampling in the
observability Terraform module (Phase 14) — never raise retention without re-checking the cost
estimate (plan §19).

## 8. Azure Monitor alerts (plan §11.7)

Alert on: **smoke failure**, **error-rate spike**, **auth-fail spike** (`auth_fail` security-event
rate), and **cost spike** (LLM `costUsd` daily sum approaching the `system_config` budget). These
are configured in the observability Terraform module (Phase 14, CI-validated, inert until the Azure
account exists).

## 9. Local development (plan §11.8)

`make local-demo` runs the console renderer at `DEBUG` with the requestId bound and no external
sink; telemetry export stays off. A test asserts the redaction processor strips fixture PHI/secrets
locally. Dashboard tests prove the foundation seed creates no operational activity, while pipeline
and mutating-endpoint tests cover evidence persistence and PHI-free audit rows.
