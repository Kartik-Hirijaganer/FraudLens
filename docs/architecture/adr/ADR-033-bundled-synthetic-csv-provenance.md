# ADR-033 — Exact bundled synthetic CSVs may use the existing LLM drafter

- **Status:** Accepted
- **Date:** 2026-10-22
- **Related:** [ADR-026](ADR-026-synthetic-only-model-egress.md) ·
  [ADR-031](ADR-031-atomic-upload-screening.md) ·
  [ADR-032](ADR-032-upload-validation-and-local-sar-templates.md)
- **Supersedes:** ADR-032's deferral of trusted synthetic CSV enrollment only.

## Context

FraudLens is a personal, synthetic-data project. It already has a live SAR drafter, a closed model
projection, deterministic factual/citation gates and authorized human approval. Ordinary uploads
cannot prove synthetic provenance, so they correctly select local templates. The portfolio needs a
small, honest way to exercise the existing live path through CSV intake.

Alternatives considered: trusting caller declarations or PII detection would weaken ADR-026;
allowlisting all API uploads would expose arbitrary facts; signed manifests and a dynamic export
registry add secret management and persistence that a fixed public demo file does not require.

## Decision

Only an upload whose complete bytes equal the reviewed, backend-bundled
[`config/demo-transactions.csv`](../../../config/demo-transactions.csv) receives the existing
`synthetic-generator` provenance at ingestion. All other files receive `api-upload` and use local
templates. The ordinary validation, safe persistence and atomic screening contracts still apply.

### Trust and routing

`GET /api/v1/transactions/template` returns the canonical UTF-8 content to authenticated ingest
users. The frontend saves it as a CSV. The bounded upload reader compares the original bytes before
discarding them. A changed value, reordered column or changed newline invalidates trust; a missing
backend fixture fails closed to local processing. No filename, checkbox, header, caller source or
detector result can grant this trust. The backend configuration is shipped in the existing image.

The public synthetic fixture is intentionally usable by every authorized tenant. Tenant identity
still comes from verified claims; storage, deduplication, jobs and evidence remain tenant-scoped.
Duplicate external ids retain their original facts and provenance. Replays create no screening work,
and a later trusted file cannot upgrade an earlier unverified row.

Model-eligible cases draw account history only from other model-eligible persisted sources. An
unverified file sharing the public fixture's account identifiers cannot enter trusted scoring or
model evidence through origin or counterparty history. Local-only investigations retain their normal
tenant-scoped history.

CSV jobs use the existing single writer and local regulatory retrieval. With live mode explicitly
enabled, trusted flagged rows reach the production provider client, evidence projection and quality
gate; with providers disabled they use the existing labeled mock. Unverified files always use the
labeled template. A failed draft preserves scoring, SHAP and its alert. Regeneration reuses saved
evidence. Reviewer/admin decisions and PDF export remain separate from generation and filing.

### Evidence and retained scope

[`test_synthetic_csv.py`](../../../tests/integration/test_synthetic_csv.py) exercises byte changes,
duplicate provenance, missing fixtures, production live drafting with a recording SDK double,
transport fallback, rejected citations, saved results, regeneration and reviewer approval. Existing
privacy/egress tests continue to cover arbitrary uploads. No paid call is claimed by these tests.

The obsolete frontend-only CSV asset is removed; download and verification use one backend-owned
file. CSV validation, full account keys, migration 0015 and local templates remain active parts of
both branches. This change adds no migration, table, signing key or dynamic export service.

## Tradeoffs accepted

- This verifies one known synthetic file, not arbitrary synthetic CSVs or anonymization. Editing it
  deliberately selects the local path. Extending trusted content requires a reviewed fixture change.
- Historical uploads remain unverified. Preserving immutable provenance can leave a duplicated demo
  row on the local path; use a fresh synthetic fixture version instead of rewriting its source.
- The application still prohibits real PHI. Live provider modes, existing governance and spend
  controls remain explicit operator choices; implementation does not authorize provider spend,
  deployment, cloud mutation or regulatory submission.

## Reconsider when

- Users need dynamic synthetic exports: introduce signed, tenant-bound exact-content manifests
  rather than broadening this match into permissive normalization.
- Real customer data is proposed: require a separately approved data-handling policy and evaluation.
- A fixture revision is needed: version its external ids, validate every field and retain the
  fail-closed behavior for previously downloaded bytes.
