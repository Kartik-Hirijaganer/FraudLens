# ADR-027 — Durable investigation execution uses leases, fencing, and bounded replay

- **Status:** Accepted
- **Date:** 2026-09-14
- **Related:** [ADR-016 — Run owns execution; SSE is a pure observer](README.md)
  · [ADR-019 — bounded multi-agent SAR drafting](ADR-019-multi-agent-sar-drafting.md)

## Context

FraudLens originally launched each accepted investigation as an `asyncio` task in the API process.
The run row and event log were durable, but execution was not: replacing an API pod could leave a
run permanently `running`, and adding API replicas could not safely recover it. The Kubernetes
demonstration needs API autoscaling and deliberate worker replacement without losing accepted work
or allowing two workers to persist conflicting results.

Investigation execution is tenant-scoped and may call a paid model. Recovery must therefore carry
the persisted `agency_id`, prevent a stale worker from writing after ownership changes, bound retries
and elapsed time, reuse already committed work, and remain explicit that a provider call cannot be
made exactly once across a process crash.

### Options considered and rejected

1. **Keep API-owned background tasks** — rejected because accepted work dies with the pod and HPA
   replicas cannot coordinate recovery.
2. **Use an in-memory queue** — rejected because it moves the same failure mode into another process
   and has no durable tenant-bound audit record.
3. **Use Redis/Celery or a cloud queue now** — rejected because Postgres already owns the run state,
   ordering, and tenant identity; another state system adds credentials, reconciliation, and failure
   modes without improving this bounded demonstration.
4. **Use leases without fencing** — rejected because an expired worker can resume after takeover and
   overwrite or duplicate the replacement worker's output.
5. **Hold one database transaction for the whole pipeline** — rejected because model calls may be
   slow, long locks reduce concurrency, and partial evidence would be lost on interruption.
6. **Claim exactly-once model inference** — rejected because a process can die after provider
   acceptance but before durable acknowledgement. The system provides at-least-once execution with
   replay and bounded, conservatively governed uncertainty.

## Decision

FraudLens supports `inline` and `worker` execution modes. `inline` preserves the local in-process
path. In `worker` mode, the API validates and commits a `pending` run before returning 202; a separate
`python -m fraudlens_backend.worker` process claims eligible rows with PostgreSQL
`FOR UPDATE SKIP LOCKED`.

Every claim contains the persisted `agency_id`, run and transaction ids, owner, incremented attempt,
monotonic fencing token, lease expiry, absolute deadline, workflow, and model override. Pipeline
dependencies are reconstructed through agency-scoped repositories. Every run-owned write locks and
checks the owner/token fence. A separate session heartbeats the lease; losing the fence cancels the
pipeline. Terminal status, terminal SSE event, and lease release commit in one transaction.

Expired leases move to `retrying` with configured backoff or to `failed` at the attempt/deadline cap.
Both workers and worker-mode API startup run the reaper. Recovery reuses singleton events and stage
records, and a persisted successful SAR is reconstructed as a cached draft so no second provider call
is made. Database uniqueness constraints backstop result, retrieval, inference, alert, draft-version,
and event identities.

Worker-mode SSE replays and polls `analysis_run_events` with bounded backoff and keepalives; the GET
snapshot remains authoritative and exposes status plus `attempt`/`maxAttempts`. No prompt, response,
raw idempotency key, or PHI is placed in a lease, event, log, or liveness file. This decision does not
change JWT authorization, tenant ownership, human review authority, the synthetic-only egress rule,
or the Azure application deployment target.

Before accepting a live multi-agent run, the API locks the tenant's `agencies` row and reserves the
validated worst-case attempt cost multiplied by the worker attempt cap in `llm_reserved_usd`. The
same transaction admits the run only when today's actual SAR spend plus all active reservations plus
the new reservation fits the tenant budget. Terminal status releases capacity by excluding the run
from active totals while retaining the reservation value as audit evidence.

### Why

**1 · Durable acceptance is separated from process lifetime.** A committed pending row survives API
replacement and is visible to any healthy worker.

**2 · Leases recover liveness while fencing protects safety.** Expiry permits takeover; the monotonic
token prevents the original worker from committing after takeover.

**3 · Tenant scope travels with ownership.** Global scheduling discovers work, but every transaction,
stage, event, and terminal write is rebound to the claim's persisted agency through the scoped
repository layer.

**4 · Recovery has hard operational bounds.** Attempt, deadline, lease, heartbeat, and backoff limits
are validated configuration, so poisoned work cannot loop forever.

**5 · Durable replay limits duplicate work and spend.** Completed stage rows and successful drafts
are reused after a crash. The PostgreSQL CI gate proves row-lock behavior that SQLite cannot model.

**6 · Spend admission is a shared database decision.** A tenant-row lock serializes concurrent API
replicas, so process-local rate limits cannot multiply paid multi-agent exposure.

## Tradeoffs accepted

- PostgreSQL is now required for multi-worker concurrency semantics. SQLite remains a deterministic
  unit-test path but cannot demonstrate `SKIP LOCKED`; a dedicated PostgreSQL 16 CI job does.
- The scheduler performs a narrow global query to find eligible runs. The resulting claim carries
  `agency_id`, and all business-data access remains tenant-scoped.
- Deterministic rules/scoring may recompute after interruption, although committed outputs and
  singleton events are reused. Successful persisted SAR drafts do not call the provider again.
- Polling durable events adds bounded database reads compared with an in-process queue. Backoff and
  connection-free sleep prevent a long-lived SSE request from pinning a connection.
- Graceful shutdown waits for an active run, but forced termination can still leave a lease until
  expiry. Recovery latency is therefore lease duration plus configured backoff.
- Exactly-once external inference is not promised. Budget controls must include uncertainty around a
  call interrupted before its result is durably recorded.
- Reserving every possible attempt is deliberately conservative and can temporarily reject work
  that would probably fit. This favors a hard tenant spend ceiling over optimistic utilization.

## Reconsider when

- Sustained queue volume or claim latency makes the Postgres scheduler a measured bottleneck; compare
  a managed durable queue while retaining the run row as authority and the same tenant/fence contract.
- Provider idempotency tokens become available end-to-end; add them as defense-in-depth but do not
  remove local fencing without failure-injection evidence.
- Recovery latency must be below the safe minimum lease duration; evaluate cooperative cancellation
  or an external worker heartbeat without weakening stale-write rejection.
- Parallel stage execution is introduced; replace singleton-stage assumptions with explicit stage
  identities and per-stage fences before enabling it.
- Real customer data or another tenant boundary enters the worker path; repeat the privacy, retention,
  authorization, audit, and failure-mode review before broadening inputs.

Implementation evidence: `tests/integration/test_run_leases_postgres.py` proves `SKIP LOCKED`,
stale-fence rejection, concurrent spend admission, worker cancellation/replacement, singleton stage
outputs, and exact persisted-event replay against PostgreSQL 16. The portable worker suite repeats
recovery deterministically on SQLite; migration tests prove the queue, reservation, and restart-safe
uniqueness fields apply and reverse.
