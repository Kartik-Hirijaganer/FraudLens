# Security hardening & threat model

> Runbook for the FraudLens security posture and the **deploy gate** it must pass. Phase 13 of
> the plan ([`plans/2026-06-12-aml-fraud-detection-system.md`](../../plans/2026-06-12-aml-fraud-detection-system.md))
> verifies and tightens the posture **before any production-style deploy** — it is a hard
> prerequisite for Phase 14 (Azure deploy). Pairs with
> [phi-guardrails.md](phi-guardrails.md), [observability.md](observability.md), and the
> governance rules in [AGENTS.md](../../AGENTS.md). Implements plan §4, §6, §8, §11, §16 Phase 13.

## 1. The deploy gate

Run the consolidated gate locally before opening a PR; CI mirrors it (the same `make` targets):

```bash
pytest tests/security        # fail-closed auth, tenant isolation, headers/CSP, rate limit, no-leak
make secrets-scan            # gitleaks (whole repo) + Infisical/config guard
make deps-audit              # pip-audit (Python) + npm audit (frontend) — needs network
make ci                      # the full read-only umbrella (lint, types, coverage, docs, tenancy, …)
```

**Acceptance:** `tests/security` green, `gitleaks` + dependency audits clean. Only then may the
Phase 14 deploy run. The suite is intentionally a *gate*, not the only coverage — each earlier
phase ships its own security tests (e.g. `test_gateway.py`, `test_logging.py`, `test_api_v1.py`,
`test_rag_citations.py`); Phase 13 re-asserts the cross-cutting guarantees end-to-end.

## 2. Trust boundary (gateway-first)

The frontend is **untrusted**; the API Gateway is the **single external entry point**; backend
services are **trusted and internal** (plan §3.1, §4). In v1 the gateway is the FastAPI middleware
stack ([`middleware/gateway.py`](../../backend/src/fraudlens_backend/middleware/gateway.py)) in
front of in-process service modules; it splits into internal-ingress service apps later (ADR-004)
with no SPA change. The edge centralizes the cross-cutting controls below so none can be skipped
or duplicated.

## 3. Controls

| Control | Where | Notes |
|---|---|---|
| **AuthN — fail-closed JWT** | [`api/deps.py`](../../backend/src/fraudlens_backend/api/deps.py) `authenticate` | Missing/invalid token → 401. The default verifier rejects every token until a signing key is wired (locked by default). |
| **Dev bypass — prod-inert** | `settings.is_dev_bypass_enabled` | False whenever `environment == "prod"` regardless of the flag. Proven by `tests/security/test_fail_closed.py` + `test_api_v1.py`. |
| **AuthZ — RBAC** | `get_admin_tenant` | `analyst \| reviewer \| admin` in the JWT claim; admin-only model/lifecycle routes return 403 `admin_role_required` for non-admins. |
| **Tenant isolation** | `enforce_tenant` → `fraudlens_core.require_agency_id` | `agency_id` comes **only** from the verified claim; cross-tenant reads return 404 (no existence leak), claim/path mismatch returns 403. Every tenant table carries indexed `agency_id` (`make tenancy-check`). |
| **Security headers + CSP** | [`middleware/security.py`](../../backend/src/fraudlens_backend/middleware/security.py) | HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and a **path-aware Content-Security-Policy**: strict (`default-src 'none'`) on the API, relaxed only on the docs UI (Swagger/ReDoc CDN). Stamped by the gateway on every response. |
| **CORS allowlist** | gateway `install_gateway` | Exact origins from config per environment (never source); empty = deny all cross-origin. |
| **Rate limiting** | gateway (global, per client) + `api/deps.rate_limit` (per route) | Fixed-window edge limiter returns a 429 envelope; the telemetry client-error sink adds a stricter per-route sliding-window limiter as defense-in-depth. |
| **PHI masking + no-leak** | `services/phi_mask.py`, `fraudlens_core.phi`, logging redaction | Account identifiers masked at ingest (masked-only storage, ADR-014); masked before any LLM/log; the structlog redaction processor is a second net. Inference/drift tables persist only hashes + metrics. |
| **RAG-as-data** | [`fraudlens_ml.rag.citations`](../../packages/fraudlens-ml/src/fraudlens_ml/rag/citations.py) | Retrieved regulation text is escaped + sentinel-fenced as data; an injection payload cannot forge the fence or issue instructions (see [phi-guardrails.md](phi-guardrails.md)). |
| **Upload safety** | [`api/v1/transactions.py`](../../backend/src/fraudlens_backend/api/v1/transactions.py) `_read_csv` | Content-type (415), byte cap (413), and row cap (413) enforced **before** parsing. |
| **Error envelope** | [`api/errors.py`](../../backend/src/fraudlens_backend/api/errors.py) + [`models/errors.py`](../../backend/src/fraudlens_backend/models/errors.py) | `{code, message, details, requestId}` only — fixed catalog messages, field/reason detail pairs, never raw input, stack traces, or exception classes. |
| **Secrets** | Infisical (runtime) | No secrets in `.env`/source/config/fixtures; `make secrets-scan` (gitleaks + guard) blocks regressions. |
| **Dependency CVEs** | `make deps-audit` (CI) | `pip-audit` over the resolved Python tree + `npm audit --audit-level=high` over production frontend deps. |
| **HTTPS/TLS** | Container Apps ingress (Terraform, Phase 14) | `allow_insecure_connections = false` + DB TLS; asserted in the Terraform plan test (Phase 14). |

## 4. Threat model (STRIDE)

| Threat | Vector | Mitigation | Residual risk |
|---|---|---|---|
| **Spoofing** | Forged/stolen identity | Fail-closed JWT verified at the edge; prod bypass inert; service identity via gateway-signed context (ADR) | Token theft within TTL — short access TTL + rotating refresh; revocation latency noted (§6.1) |
| **Tampering** | Mutated request / cross-tenant write | `extra="forbid"` Pydantic boundaries; `agency_id` from claim only; append-only audit/alert-action tables | Compromised gateway — mitigated by least privilege + internal-only services |
| **Repudiation** | "I didn't do that" | Immutable `audit_logs` (actor, action, resource, requestId — no PHI); SAR human sign-off; request-id correlation | Audit completeness depends on consistent helper use (tested in `test_audit_consistency.py`) |
| **Information disclosure** | PHI/secrets in logs, errors, prompts, artifacts | PHI masking + redaction processor; masked-only storage; error envelope no-leak; hash-only inference/drift; no third-party analytics | A novel free-text PHI shape — optional Presidio NER (off by default) closes most of the gap |
| **Denial of service** | Request flood / LLM-budget exhaustion | Edge + per-route rate limits; LLM session/daily USD budget guard (429); scale-to-zero absorbs idle | Per-replica counters in v1 (single replica); shared store is the multi-replica scale-up |
| **Elevation of privilege** | Non-admin reaching admin/model APIs; RAG prompt injection | RBAC re-checked in services (403); RAG-as-data fencing; deterministic core (LLM only at the edge) | Misconfigured role claim — least-privilege default (`analyst`) when the role is absent |

## 5. Residual risk & honest limits

Passing the gate **reduces** risk; it does not make the system invulnerable (plan §16 Phase 13
risk: "false sense of security"). Known v1 limits, accepted by design:

- **Token revocation latency** — stateless JWT trades instant revocation for cost/scale (§6.1).
- **Per-replica rate-limit counters** — correct for the single-replica, scale-to-zero v1; a
  shared store (e.g. Redis) is the documented multi-replica upgrade.
- **CSP `'unsafe-inline'` on the docs UI** — required by Swagger UI; scoped to the docs paths
  only, never the JSON API surface.
- **Synthetic data only** — no real PHI is processed; the PHI controls are defense-in-depth for
  the healthcare-adjacent posture, not a substitute for a BAA-covered provider if real PHI is
  ever introduced (then flip to Azure OpenAI per ADR-003/§7.1).
- **Deploy-time controls** (HTTPS `allowInsecure=false`, DB TLS, managed identity) are asserted
  in the Terraform plan and exercised in Phase 14, not this suite.

### 5.1 Suppressed advisory: ChromaDB CVE-2026-45829

`make deps-audit` suppresses one advisory via `--ignore-vuln CVE-2026-45829`, with this
assessment: the CVE is a **pre-authentication RCE in the ChromaDB HTTP server's**
`/api/v2/.../collections` endpoint, exploited by POSTing a malicious model repository with
`trust_remote_code=true`. **FraudLens does not run the ChromaDB server** — the FinCEN/BSA index
is an embedded, read-only persistent index baked into the image (plan §3), accessed in-process
by the retriever; the vulnerable HTTP endpoint is never exposed and `trust_remote_code` is never
set. No fix version is published, so an upgrade is not yet available. **Re-evaluate** when a
fixed ChromaDB ships (drop the ignore and bump) or if the architecture ever exposes the ChromaDB
server. Every other (non-ignored) advisory still fails the gate.
