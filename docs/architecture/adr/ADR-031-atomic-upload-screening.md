# ADR-031 — CSV acceptance includes durable screening work

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related:** [ADR-027](ADR-027-durable-investigation-execution.md), [ADR-026](ADR-026-synthetic-only-model-egress.md)

## Context

CSV ingestion persisted transactions without scheduling analysis, while opening a transaction
started another run even when saved results existed. Request-owned tasks would leave a failure
window between ingestion and scheduling, and a single capped batch sweep would strand later rows.

Alternatives considered were a tenant-wide sweep after each upload, a separate queue service, and
browser-triggered investigations. Sweeps include unrelated records; another queue introduces a
second persistence boundary; browser scheduling depends on the user keeping the page open.

## Decision

Commit each CSV import, its accepted transactions, and one pending investigation per accepted row
in one database transaction. Link runs to the existing import job and use transaction-specific
idempotency keys. Duplicates and rejected rows enqueue no work. Enqueue limits bound chunk size;
worker claim limits bound each scheduling pass, and neither truncates the accepted workload.

Automatic runs start their execution deadline at the first claim. Retries retain that deadline and
the existing attempt cap, backoff, lease and fencing contracts. Worker mode uses the existing worker;
inline mode supervises the same worker for upload-associated runs during application lifespan.
No new cloud job or standing resource is introduced. Login and observation endpoints create no work.

Optional drafting is constructed after deterministic analysis and alert creation. CSV provenance
remains `api-upload`, so live drafting is refused before provider access under ADR-026. Budget and
policy refusals become persisted drafting outcomes without invalidating the score or alert.

Transaction navigation prefers active progress, then the saved latest-run pointer, then the most
recent historical run. A separate permission-gated Re-run action creates another historical run.
Transaction locks prevent competing active runs; idempotency keys identify retried user actions.
Model overrides require admin authority on the server as well as in the UI.

### Evidence

The [upload tests](../../../tests/integration/test_upload_screening.py) cover complete queue
admission across limits, duplicates, rollback, tenant scope and history. The
[runtime tests](../../../tests/integration/test_upload_runtime.py) cover inline recovery and soft
failures. The [PostgreSQL tests](../../../tests/integration/test_upload_postgres.py) exercise
concurrent uploads and reruns. Operational behavior is documented in the
[screening runbook](../../runbooks/transaction-screening.md).

## Tradeoffs accepted

- A CSV request persists its bounded set of run rows before acknowledgement, adding database work
  to ingestion in exchange for atomic acceptance.
- Queue waiting has no execution deadline; health and progress expose a stalled worker. Claimed
  work retains bounded execution and recovery.
- Inline progress uses persisted-event polling because the worker does not publish through the
  interactive RunManager. Frontend observation is read-only and stops after the backlog drains.
- This supersedes ADR-027's enqueue-time deadline for automatic upload runs only. Explicit manual
  investigations retain their acceptance-time deadline. Provider execution remains at least once.
- Uploads can have completed risk analysis with blocked drafting. The UI must display both states
  and must not imply that a SAR was drafted, approved or filed.

## Reconsider when

- Measured CSV admission latency warrants durable chunk expansion rather than per-row acceptance.
- Queue age requires an operational SLA separate from bounded execution time.
- Throughput requires a dedicated worker deployment for the permanent application; retain the
  same database acceptance and fence contracts and review recurring cost before provisioning.

## Amendment — 2026-10-21: drafting outcomes

[ADR-032](ADR-032-upload-validation-and-local-sar-templates.md) extends this workflow with strict
upload validation and a provider-free template for unverified uploads. It supersedes the expectation
that every uploaded case must finish with blocked drafting: verified local evidence can now produce a
quality-gated template. External AI remains refused, and evidence failure preserves deterministic
results and alerts. Atomic admission, deadlines, observation and explicit rerun contracts are unchanged.
