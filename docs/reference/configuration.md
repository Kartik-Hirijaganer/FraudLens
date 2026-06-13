# Configuration & error reference

> Non-secret configuration surface and the API **error-code catalog** (plan §5, §12, §16
> Phase 3). Secrets never live here — they come from **Infisical** at runtime (Golden
> Rule 2); only non-secret config lives in `config/*.yaml` + `FRAUDLENS_*` env. This page is
> hand-maintained; the error catalog mirrors
> [`models/errors.py`](../../backend/src/fraudlens_backend/models/errors.py).

## Error envelope

Every error response is the FraudLens envelope — never a raw stack trace or exception name:

```json
{ "code": "duplicate_external_id", "message": "...", "details": [{"field": "...", "message": "..."}], "requestId": "..." }
```

- `code` — stable, machine-readable (clients branch on this, not the HTTP status alone).
- `message` — fixed, human-readable, **PHI-free**.
- `details` — optional `{field, message}` pairs only; **never the rejected input value**, so
  PHI cannot leak through a validation error.
- `requestId` — the gateway correlation id (echoed from / set in `X-Request-Id`).

## Error-code catalog

Business codes raised by the ingestion surface (`models/errors.py`). Generic transport codes
(`unauthorized` 401, `forbidden` 403, `not_found` 404, `validation_error` 422,
`rate_limited` 429, `service_unavailable` 503, `internal_error` 500) are rendered by the
status-driven handler and apply across every route.

| Code | HTTP | Raised when |
|------|------|-------------|
| `duplicate_external_id` | 409 | Ingesting a transaction whose `(agencyId, externalId)` already exists. |
| `transaction_not_found` | 404 | `GET /transactions/{id}` for a missing id, or one owned by another agency (no existence leak). |
| `batch_too_large` | 413 | A `/transactions/batch` body exceeds `ingestMaxBatchSize`. |
| `payload_too_large` | 413 | A `/transactions/upload` body exceeds `ingestCsvMaxBytes`. |
| `too_many_rows` | 413 | A CSV upload has more data rows than `ingestCsvMaxRows`. |
| `empty_payload` | 422 | A CSV upload parses to zero data rows. |
| `invalid_csv` | 422 | A CSV upload has no header row / cannot be parsed. |
| `unsupported_content_type` | 415 | A CSV upload is not sent as `text/csv`. |
| `rule_not_found` | 404 | `GET`/`PATCH`/`DELETE /rules/{id}` for a missing id, or one global/owned by another agency (no existence leak). |
| `duplicate_rule_code` | 409 | Creating a rule whose `code` already exists for the agency. |
| `model_version_not_found` | 404 | `GET /model-versions/{id}` for an id absent from the global registry. |
| `validation_error` | 422 | A field fails validation (Pydantic structural or canonical-schema semantic, e.g. non-ISO currency, non-positive amount, future `occurredAt`). |

Batch and CSV ingestion are **partial-accept**: valid rows persist while invalid rows are
returned as bounded, PHI-free `sampleErrors` (`{index, externalId?, code, message}`).

## Ingestion limits (`FRAUDLENS_*` / `config/*.yaml`)

Caps are configuration, never hardcoded (plan §12.1); override per-environment.

| Setting (env `FRAUDLENS_*`) | Default | Purpose |
|------|---------|---------|
| `INGEST_MAX_BATCH_SIZE` | 500 | Max transactions per `/transactions/batch` request. |
| `INGEST_CSV_MAX_BYTES` | 5 242 880 | Max `/transactions/upload` body size (bytes). |
| `INGEST_CSV_MAX_ROWS` | 10 000 | Max data rows per CSV upload. |
| `INGEST_SAMPLE_ERRORS_LIMIT` | 10 | Max per-row rejection samples returned. |
| `CLIENT_ERROR_MAX_MESSAGE_LENGTH` | 2 000 | Truncation cap for the client-error sink message. |

## PHI masking (ingest)

Account identifiers are masked **before persistence** and raw PHI is never stored
(ADR-014). Masking is deterministic and in-process — regex + `python-stdnum` (Luhn for
cards, IBAN checksum) in [`fraudlens_core.phi`](../../packages/fraudlens-core/src/fraudlens_core/phi/masking.py),
orchestrated by [`services/phi_mask.py`](../../backend/src/fraudlens_backend/services/phi_mask.py).
A `featureHash` (PHI-free content fingerprint) is stored alongside the masked fields. See
the [PHI & guardrails runbook](../runbooks/phi-guardrails.md) (lands with later phases) for
the full leakage-prevention model and the optional Presidio NER enhancer (`phiNerMasking`).

## Other config surfaces

- **Gateway routes / CORS / rate limits / security headers** — boot-critical typed
  config (`config/gateway/routes.yaml` + `FRAUDLENS_*`), loaded at startup, never
  DB-dependent (plan §4.3, §12.3).
- **Runtime tunables** (risk-band thresholds, budgets, model gates, feature flags) live in
  the `system_config` table with safe cached in-process defaults (plan §9.1).
- **No-hardcoding policy** — absolute URLs, IPs, and LLM model ids must come from
  config/env, enforced by `scripts/check_no_hardcoding.py` + ruff `PLR2004`.
