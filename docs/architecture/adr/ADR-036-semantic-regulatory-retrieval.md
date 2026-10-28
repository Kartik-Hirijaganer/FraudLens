# ADR-036 — Semantic regulatory retrieval with lexical fallback

- **Status:** Accepted
- **Date:** 2026-10-26
- **Related:** [ADR-023](ADR-023-sar-quality-and-privacy-gates.md),
  [ADR-026](ADR-026-synthetic-only-model-egress.md),
  [ADR-029](ADR-029-recurring-operational-budget.md),
  [ADR-032](ADR-032-upload-validation-and-local-sar-templates.md)

## Context

The default hashing embedder stores word-count features in a vector database but cannot learn
relationships between differently worded descriptions. The live semantic adapter already existed,
yet production builds and the demo story selected hashing. Flipping only the runtime flag would
produce incompatible vectors, lexical fallback, and a failed required-index readiness check.

Keeping hashing everywhere was considered but retains the paraphrase limitation. Building semantic
vectors through a paid provider inside ordinary CI/image builds was rejected because it makes
reproducibility depend on credentials, provider availability, and unapproved recurring spend. A
semantic/lexical rank-fusion retriever adds tuning scope beyond the requested fallback contract.

## Decision

**Default authorized live retrieval to `text-embedding-3-small` semantic vectors and use deterministic
local lexical retrieval when embedding provenance, provider access, or budget admission fails.**

### Index and deployment contract

The non-secret model/dimension/version selection remains in `config/llm/rag.yml`. Reviewed learned
vectors for the public regulatory corpus are committed with exact text hashes and provenance;
`make ingest-rag` materializes them keylessly, including inside the network-disabled Docker stage.
An explicit approved `make ingest-rag-live` refreshes the public snapshot. It never embeds case data.
Document embedding and validation finish before an existing collection is replaced. The demo story
selects this same semantic space. Explicit offline local development and test fixtures retain their
hashing override; uploads and source-restricted cases retain provider-free lexical retrieval.

### Cost and runtime contract

Query vectors use a bounded content-hash LRU. Before each cache miss, a deployment-wide global
`system_config` counter reserves UTF-8-byte-bounded token cost including configured transport retries.
One deterministic primary key and a row lock serialize PostgreSQL writers; SQLite's upsert write
serializes local tests. The counter contains only UTC day and reserved USD, has `agency_id IS NULL`,
and survives process restarts. Missing/invalid state or an exhausted $0.05/UTC-day allowance denies
the provider call and selects lexical fallback. Failed calls retain their reservation. The existing
SAR allowance is unchanged; the new operational ceiling adds at most $1.55 per 31-day month. No
cloud budget resource or deployment was changed to implement this source change.

Synchronous retrieval runs in worker threads so provider latency does not stall async heartbeats.
App shutdown releases the embedding loop and budget connection pool. Corpus refresh is a separate,
human-approved paid maintenance action and is not covered by the recurring query allowance.

### Evidence

The [published authored-query comparison](../../reference/benchmarks/semantic-rag-default.md)
measured relevant-document-first results of 9/10 for semantics, 4/10 for hashing, and 3/10 for
lexical overlap. The [offline replay test](../../../tests/unit/test_rag_relevance.py) reproduces
actual provider vector rankings. [Budget tests](../../../tests/integration/test_rag_query_budget.py)
exercise concurrent admission, restarts, UTC rollover, and denial without storage. Existing upload
egress and SAR quality tests remain the privacy and grounding contracts.

## Tradeoffs accepted

- A cache miss adds provider and budget-database latency; local lexical retrieval remains available.
- Reviewed public embedding artifacts enlarge the repository and must refresh when chunk text or
  embedding provenance changes. Semantic similarity does not establish regulatory applicability.
- Reservations intentionally overestimate actual spend, so lexical fallback can start before the
  provider has billed $0.05. This is preferable to undercounting retries or resetting on restart.
- The small relevance sample supports a regression contract, not a general accuracy claim; the
  existing cosine floor is retained until broader independently labeled data supports calibration.

## Reconsider when

- More than 5% of authorized searches fall back because the embedding allowance is exhausted.
- Broader labeled evaluation shows poorer relevance, wrong-jurisdiction matches, or weak abstention.
- Exact citation lookups justify metadata matching or lexical/semantic rank fusion.
- Local learned embeddings meet measured CPU, memory, image-size, and latency limits and remove
  provider dependence without weakening retrieval quality.
