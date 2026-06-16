# Troubleshooting

Use this runbook when local demo, tests, or CI gates fail. Start with the symptom, verify the
specific command output, then make the smallest scoped fix.

## Quick Triage

| Symptom | Check | Likely Fix |
| --- | --- | --- |
| Backend will not start | `uv run uvicorn fraudlens_backend.main:app --reload` or `make local-demo` output | Invalid config value, missing dependency sync, or occupied port. |
| `/readyz` is `down` | `curl http://localhost:8000/readyz` | Start Postgres, fix `DATABASE_URL`, or rebuild the RAG index if required. |
| Auth returns 401 | Response body `code` and logs for `auth_fail` | Provide a bearer JWT, configure `FRAUDLENS_AUTH_JWKS_URL`, or use dev bypass only in non-prod. |
| Cross-tenant request returns 403 | Compare path tenant id to JWT `agency_id` | Use the tenant from the verified token; never send arbitrary tenant ids. |
| Tests fail on stale docs | `make docs-check` | Run `make docs`, inspect the generated diff, commit only intentional doc changes. |
| Dependency audit fails | `make deps-audit` | Upgrade the vulnerable package or document a narrowly scoped ignore with rationale. |
| Frontend cannot reach API | Browser network panel and Vite env | Confirm API origin, CORS allowlist, and backend port. |

## Local Demo Recovery

```bash
make local-demo-down
make local-demo-reset
uv sync --all-packages
npm --prefix frontend ci
make local-demo
```

`local-demo-reset` drops the local Postgres volume and `.local/` artifacts. It is safe for synthetic
demo data, but do not use it against any shared database.

## Database Issues

| Failure | What To Do |
| --- | --- |
| Migration error | Run `uv run alembic heads`; there must be exactly one head. Inspect the failing migration before editing. |
| Tenant data missing | Run `make db-seed` or `uv run python scripts/seed.py` against the local database. |
| Cross-tenant read leak suspicion | Run `make tenancy-check` and targeted integration tests for the affected repository/router. |

## API Contract Issues

```bash
make openapi
uv run pytest -q tests/integration/test_api_v1.py --no-cov
```

If a path parameter appears as snake_case in OpenAPI, the route path should use camelCase and the
handler parameter should use `Path(alias="...")`. Keep Python variables snake_case.

## PHI Or Audit Issues

| Check | Command |
| --- | --- |
| Audit consistency | `uv run pytest -q tests/integration/test_audit_consistency.py --no-cov` |
| PHI leakage guards | `uv run pytest -q tests/security/test_no_leak.py tests/security/test_input_safety.py --no-cov` |
| Secret/config guard | `make secrets-scan` |

Audit metadata must contain only ids, counts, enum/status values, and safe scopes. Never place
account identifiers, notes, SAR content, prompts, request bodies, or config values in audit metadata.

## When To Escalate

Escalate before proceeding if a failure suggests real PHI, a committed secret, cross-tenant data
visibility, a production dev-bypass path, or a dependency CVE without an upgrade or defensible
non-exposure rationale.
