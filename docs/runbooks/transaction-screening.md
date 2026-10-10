# CSV screening and saved investigations

CSV upload accepts valid new rows and queues their investigations atomically. Closing the browser
or restarting the API does not remove accepted work. Single-record and JSON batch ingestion retain
their existing behavior; signing in never schedules analysis or backfills old transactions.

## User workflow

1. Import a synthetic CSV from Transactions. The acknowledgement distinguishes accepted,
   duplicate and rejected rows from background screening completion.
2. Watch queued/running/retrying/completed/failed counts. The transaction list, Dashboard and Alerts
   refresh from persisted results while screening is active. Filters and paging remain intact.
3. Open **View progress** for active work, **View results** for saved analysis, or **View failed run**
   for a failure. These actions observe existing runs and never POST another investigation.
4. Use **Re-run** explicitly after the current run is terminal. Analysts and reviewers use normal
   active deployment routing. Only admins can select a model for the next investigation. Selection
   itself does not score anything; previous runs remain addressable at their original URLs.
5. Auditors can open saved evidence but cannot upload, investigate or rerun.

The deterministic rules → XGBoost → SHAP pipeline persists scores and raises threshold-crossing
alerts before optional regulatory retrieval and SAR drafting. Below-threshold rows finish without
an alert or SAR. A drafting refusal or provider failure leaves the risk analysis and alert usable.
Under the current [egress policy](phi-guardrails.md), CSV/API-upload provenance is not eligible for
live LLM transport. Expect an explicit blocked-drafting reason rather than a live SAR. Mock mode
remains keyless. A draft is never an approval or regulatory filing.

## Runtime and recovery

- Worker mode: use the existing `make worker` process with the same settings/database as the API.
- Inline mode: API lifespan supervises an upload-only durable worker automatically.
- Production worker liveness writes to `/tmp/fraudlens-worker/heartbeat`, outside the non-root
  image's application directory. Kubernetes overrides this with its writable volume path.
- `batch_score_limit` is the upload enqueue chunk size; all chunks are accepted. `run_claim_batch`
  limits a scheduling pass, and subsequent passes drain remaining work.
- Automatic execution deadlines start on first claim and are retained across retries. Lease expiry,
  retry backoff, maximum attempts and terminal events follow the durable-worker contract.
- Read `GET /api/v1/transactions/screening` for tenant counts, or supply `jobId` from the CSV response
  for that upload. Another tenant's job is indistinguishable from a missing job.
- If progress stops, check worker liveness and database availability. Read-only retries/focus checks
  reconnect observation; they do not enqueue replacement work. Terminal core failure requires an
  authorized, explicit rerun.

## Local verification

Use synthetic fixtures and mocked providers. Run `make pre-pr` and `make test-coverage-diff`.
For locking evidence, point both database variables at an isolated local PostgreSQL database and
run `make db-migrate postgres-run-test`. Never point these mutating migration/test commands at
Supabase or another live database. The tests do not authorize cloud jobs or paid provider calls.
