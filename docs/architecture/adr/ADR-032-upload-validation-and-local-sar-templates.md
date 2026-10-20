# ADR-032 — Unverified uploads use closed validation and local SAR templates

- **Status:** Accepted
- **Date:** 2026-10-21
- **Trusted CSV enrollment:** Deferral superseded by [ADR-033](ADR-033-bundled-synthetic-csv-provenance.md).
- **Related:** [ADR-014](README.md) ·
  [ADR-026](ADR-026-synthetic-only-model-egress.md) ·
  [ADR-031](ADR-031-atomic-upload-screening.md)

## Context

CSV parsing previously buffered the request before checking size, replaced invalid UTF-8, accepted
arbitrary feature columns, and could reflect external identifiers in rejection samples. Shared
canonical ingestion intentionally rounds dataset-adapter amounts and assumes UTC; those conveniences
must not silently change uploaded financial facts. Account history grouped last-four display masks,
which can merge unrelated accounts. Refusing external drafting preserved scoring but left uploads
without a useful narrative.

Alternatives rejected: treating validation or masking as anonymity proof; adding `api-upload` to the
synthetic allowlist; trusting a checkbox, file name or caller manifest; retaining arbitrary notes or
features; using display suffixes for identity; and bypassing the normal SAR quality gate for templates.
A signed synthetic-export workflow is deferred rather than accepting unverifiable provenance.

## Decision

Accept only the eight documented CSV transaction columns under byte, row, column and per-field
limits; validate financial facts without rounding or inventing a timezone; persist opaque upload
identifiers; and route unverified sources to provider-free, quality-gated SAR templates.

### Validation and storage

The reader bounds incoming chunks before buffering. The CSV grammar bounds cell accumulation and
columns while parsing, checks strict UTF-8, quotes, unique complete headers and exact row widths,
and stops at the row cap. Structural errors reject the whole request before database admission.
Semantic row errors remain partial acceptance, with bounded index/physical-line/fixed-reason samples
and null external identifiers. Accepted transactions and durable screening runs still commit together.

Single, batch and CSV API ingestion share core upload validation. Identifiers use a bounded ASCII
identifier grammar; currency and country use explicit supported ISO subsets; channel uses the existing
scorer vocabulary. Amounts must be positive, finite, fit `Numeric(18,2)` and already represent exact
cents. Timestamps require an explicit offset and cannot be future dates. Uploaded features have a
closed empty schema: the production scorer derives its numeric features from facts and history.
Internal dataset adapters retain their separately documented normalization contract.

Upload external ids and full account identities use tenant-scoped, domain-separated SHA-256 digests,
reusing the content-fingerprint convention. Both upload account display fields are withheld entirely.
Every new ingest writes full account keys; history queries and direction selection use those keys,
never display masks. Migration 0015 adds nullable indexed keys without a speculative backfill.
Historical rows lacking keys cannot safely participate in account history; saved results remain.
No original CSV, unmasked upload identifier, arbitrary feature key or uploaded text is persisted.

### Local evidence and drafting

`project_for_evidence` builds the same closed typed facts, case aliases, controlled rule reasons,
allowlisted numeric SHAP and digest/metadata-verified public citations as the model projection. It
performs no source authorization. `project_for_model` still authorizes persisted provenance first;
all live writers, agents, retries, fallbacks and caches retain that boundary. The agent adapter checks
before invoking its graph, and a provenance refusal cannot select a live fallback.

Unverified cases select local lexical retrieval from the baked public index, with no embedding
provider. The regulation-search tool also checks persisted source before retrieval. Missing local
index/citations or tampered evidence produces an explicit failed draft while scores, SHAP and alerts
remain available. Regeneration uses saved retrieval evidence and never launches retrieval or scoring.

`TemplateSarDrafter` reuses deterministic composition and the production quality gate. It consumes
only the projection, labels the narrative **Template-generated draft**, records model `template`,
a versioned local composition contract, null provider, zero provider tokens and zero cost. Unknown
identity, ownership and purpose remain explicit. API/UI mode metadata survives reviewer edits, which
create another version and reset automatic quality status. Existing reviewer/admin decisions, audit
history and local PDF rendering remain; there is no automatic approval or regulatory submission.

### Evidence

- [CSV grammar and fact tests](../../../tests/unit/test_csv_validation.py)
- [Privacy, linkage and reviewer workflow](../../../tests/integration/test_upload_privacy.py)
- [Local-template and failure tests](../../../tests/unit/test_upload_drafting.py)
- [Socket-denied transport tests](../../../tests/quality/test_model_egress.py)
- [Operational walkthrough](../../runbooks/transaction-screening.md)

## Tradeoffs accepted

- Opaque upload references cannot be searched by the caller's original id. Re-upload deduplication
  remains exact within the tenant. This changes API display behavior deliberately for privacy.
- SHA-256 keys prevent suffix collisions but are pseudonyms: low-entropy identifiers remain subject
  to guessing by someone with database access. They are not anonymisation proof, and real PHI remains
  prohibited. Keyed identity/rotation requires its own secret-management and migration design before
  any separately approved production-data expansion.
- Existing masked records cannot yield reliable full identities. Re-ingest synthetic fixtures through
  a governed path when history is needed; do not change their provenance or merge on suffixes.
- Supported ISO subsets intentionally reject unsupported codes. Adding a code or model feature needs
  an explicit shared typed contract, scorer review, documentation and adversarial tests.
- Local templates provide grounded drafts, not AI-generated synthesis or a legal determination.
  The baked corpus must be available and verified. Human edits invalidate automatic quality evidence;
  a reviewer can explicitly approve an edited version under the existing human-review contract.

## Reconsider when

- A server-generated synthetic CSV export with a signed manifest bound to exact content bytes is
  implemented and verified against tampering, replay and tenant ownership. Only then consider a new
  immutable trusted source; ordinary and historical uploads remain untrusted.
- Real production data is proposed. Require separate human-approved provider/data-handling policy,
  contracts, retention, regional and access controls before enabling any external egress.
- Measured parser or admission latency requires streaming database staging. Preserve all-file
  structural validation and atomic accepted-transaction-plus-screening admission.
