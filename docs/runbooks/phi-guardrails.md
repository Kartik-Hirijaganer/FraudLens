# PHI guardrails & RAG-as-data

> Runbook for the FraudLens guardrail layer: how regulatory retrieval (RAG) is treated as
> **data, never instructions**, and how PHI is kept out of prompts, logs, and artifacts.
> Pairs with [model-lifecycle.md](model-lifecycle.md) and the governance rules in
> [AGENTS.md](../../AGENTS.md). Implements plan §8 (Guardrails & PHI Protection) and the
> Phase 6 RAG layer (§16). The synthetic-only outbound boundary is specified by
> [ADR-026](../architecture/adr/ADR-026-synthetic-only-model-egress.md).

## 1. Why this matters

The investigation pipeline assembles a Suspicious Activity Report (SAR) prompt from selected
transaction facts, controlled rule findings, model SHAP features, and retrieved FinCEN/BSA
regulation excerpts. Those inputs are untrusted in different ways:

- **Transaction fields** may contain PHI/PII. Masking at ingest protects storage, while the model
  boundary positively selects an identifier-free `SarModelInput`; masking alone is not treated as
  proof of anonymisation.
- **Retrieved regulation text** is content we index, but a poisoned or mis-curated corpus chunk
  could try to smuggle instructions into the prompt ("ignore previous instructions and …").
  This is the **prompt-injection-via-RAG** risk (plan §8.1, §21).

The defense for the second is the subject of this runbook: **RAG-as-data**.

## 2. RAG-as-data: retrieved text is reference data, never instructions

Retrieval lives in [`fraudlens_ml.rag`](../../packages/fraudlens-ml/src/fraudlens_ml/rag).
Two functions in `citations.py` enforce the boundary:

- **`escape_as_data(text)`** neutralizes a snippet before it can reach a prompt:
  - strips control characters,
  - escapes the markup/delimiter characters `&`, `<`, `>` (so a chunk can never emit a raw
    angle-bracket), and
  - caps the length so one chunk cannot dominate the prompt or the token budget.
- **`extract_citations(chunks)`** dedupes the retrieved chunks and escapes each snippet, so the
  only regulation carrier that leaves the RAG layer is already inert data.

The drafting prompt renders those citations itself, one delimited block per citation:

```
Regulations (cite ONLY these ids verbatim):
- 31 CFR 1010.314: Structuring transactions to evade reporting requirements (FinCEN)
  <regulation-data>…escaped snippet…</regulation-data>
```

Because every snippet has its `<`/`>` escaped, **no chunk can forge the closing delimiter or break
out of the data block**, and the hashed system template instructs the model to treat excerpts as
quoted reference data rather than instructions.

The stronger guarantee sits one layer earlier: `project_for_model` refuses the whole request with
`egress_regulation_not_allowed` unless a citation's exact escaped snippet digest and metadata match
a chunk from the committed corpus. An uncommitted or tampered excerpt is **refused, not fenced**.
Release 0.5.0 removed the second pre-rendered block (`SarInput.rag_context` and the helper that
built it): production had stopped rendering it, so it was an unread copy that could only drift.

This composes with the existing `fraudlens-llm/security/` guardrails (`prompt_risk.py` scans
the assembled prompt; output guardrails scan the draft and verify citation grounding), giving
defense in depth: escape at the source **and** scan the assembled prompt.

**Test coverage:** `tests/unit/test_rag_citations.py` asserts injected markup is escaped, the
fence cannot be forged, and control characters are stripped. The end-to-end injection-neutralized
assertion is part of the Phase 13 security suite (plan §17).

## 3. Threat model and model-egress boundary

The outbound threat model includes caller-controlled uploads, legacy rows with no provenance,
identifier-shaped or arbitrary free text, poisoned regulation snippets, prompt injection in model
output or tool results, cache collisions across tenants/evidence, and provider retries/fallbacks that
could otherwise reconstruct a broader request.

`transactions.source` is written by the ingest/import path and cannot be supplied as a model data
classification. `unknown` and `api-upload` are not live-egress eligible. Before the first model call,
`project_for_model` checks the recorded source against `config/llm/egress.yml`, verifies regulation
digests against the committed corpus, and constructs a frozen `SarModelInput` with unknown fields
forbidden. Disallowed source returns `egress_source_not_allowed` before transport; the rules/scoring
analysis remains usable and the keyless mock remains available.

### Field-flow inventory

| Internal input | Outbound representation | Rule |
|---|---|---|
| Tenant, user, transaction and database ids | Omitted; static case/subject/counterparty aliases | Never model-visible. |
| Amount, currency, country, direction, occurrence time | Typed verified transaction facts | Selected from persisted/pipeline state. |
| Channel | Controlled categorical value or explicit `unknown` | Arbitrary text is not forwarded. |
| Rule hit | `AmlRuleType`, controlled severity, policy-owned reason template | Raw code/reason text omitted. |
| SHAP contribution | Numeric value for a name in `FEATURE_NAMES` | Unknown feature names omitted and disclosed as unknown. |
| Regulation result | Citation id/title/source plus escaped excerpt and SHA-256 digest | Exact committed corpus match required. |
| Account ids, names, contacts, notes, upload text, edited narrative, labels | Omitted | No raw or partially masked value crosses the boundary. |
| Agent tool result | Field allowlist plus case-scoped evidence aliases | Masked and fenced before resubmission. |
| Model output sent to another model | Recursively remasked/scrubbed | Applies to tool turns and writer/reviewer handoffs. |

Retry and fallback paths reuse the already projected provider messages. Draft-cache keys include
tenant, exact projected evidence, prompt hash, requested model, and generation settings. Application
telemetry records model/prompt hashes, tokens, cost and safe reason codes—not raw prompt or response
bodies. Run `make quality-gates` for socket-denied byte-level verification.

## 4. PHI is never in the corpus, the index, prompts, or logs

- The corpus under [`data/regulations/`](../../data/regulations) is **public U.S. regulatory
  text** — there is no PHI in it by construction.
- Retrieval returns only regulation chunks + citations; it adds **no transaction data**.
- Transaction model input is reconstructed from the egress allowlist, then passed through the
  deterministic masker again as defense in depth (`fraudlens-core/phi`).
- The `job_executions(ingest_rag)` row records **counts and paths only** — never document
  content.

## 5. Limitations

- Deterministic patterns and optional Presidio are detection layers, not proof of anonymisation.
- A permitted synthetic source means the ingest route attested that source; it does not make an
  arbitrary API upload safe. `api-upload` therefore remains blocked.
- These controls and their fixtures do not authorize real customer, medical, or production case
  data. Such use requires a new privacy/compliance decision and provider contract review.
- Public regulation corpus matching establishes content lineage, not legal correctness or current
  applicability. A human reviewer remains responsible for the SAR.
- Model output can still be incorrect. Grounding and unsupported-claim checks reduce specific
  failure modes but do not make a draft an approved filing.

## 6. RAG retrieval: graceful degradation around a deterministic core

Retrieval is a **soft enhancer** (plan §10.6): a failure never fails the investigation, it
only changes which citations appear. `Retriever.retrieve` degrades in three documented modes,
surfaced on the result as `mode`:

| `mode` | When | Behavior |
|--------|------|----------|
| `vector` | Index present, query embedder healthy | Cosine top-k over the embedded chunks. |
| `lexical` | Index present, **embeddings provider down** | Deterministic token-overlap ranking over the baked chunks (no embeddings needed). |
| `empty` | Index missing or empty | Returns `[]` — the SAR notes "regulatory citations unavailable"; the decision is still produced. |

The query embedder is an injected `Embedder` (the seam a live `text-embedding-3-small`
embedder plugs into on the compliance path). Locally and in tests the **offline
`HashingEmbedder`** is used — deterministic, no keys, no network — so the index builds and
retrieves identically everywhere.

## 7. Building & shipping the index

- **`make ingest-rag`** (`scripts/ingest_rag.py`) loads the corpus, chunks it deterministically,
  embeds the chunks, and persists a ChromaDB collection at `FRAUDLENS_RAG_INDEX_DIR`
  (default `.local/chroma`). It records a `job_executions(ingest_rag)` row when a database is
  configured, and builds the index regardless.
- **`make local-demo`** builds the index during setup, so the demo ships a working fixture
  index with no keys.
- **Production** bakes the index into the container image; `rag_index_required: true` makes a
  missing/empty index fail **`/readyz`** so a broken deploy never serves without citations
  (plan §10.6). Locally `rag_index_required` is `false`, so an un-built index reports
  `skipped`, not `down`.

## 8. Configuration (all non-secret, config-driven)

| Setting (`FRAUDLENS_*`) | Default | Purpose |
|-------------------------|---------|---------|
| `rag_corpus_dir` | `data/regulations` | Committed source corpus directory. |
| `rag_index_dir` | `.local/chroma` | Persisted ChromaDB index directory. |
| `rag_collection` | `fincen_bsa` | ChromaDB collection name. |
| `rag_version` | `rag-v1` | Corpus/index version recorded on each retrieval. |
| `rag_index_required` | `false` (prod: `true`) | Fail `/readyz` when the index is absent. |

Chunk geometry (size/overlap) and top-k are algorithmic constants in `fraudlens_ml.rag` and
overridable via `ingest_rag.py` CLI flags.
