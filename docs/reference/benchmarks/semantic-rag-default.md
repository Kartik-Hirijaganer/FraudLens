# Semantic regulatory retrieval default

Measured 2026-10-26 against the committed six-document, 12-chunk public corpus.

| Retrieval | Relevant document first | Relevant document within four chunks |
| --- | ---: | ---: |
| Semantic (`text-embedding-3-small`, 1536 dimensions) | 9/10 | 10/10 |
| Previous hashing vectors | 4/10 | 7/10 |
| Token-overlap lexical fallback | 3/10 | 7/10 |

Ten fixed public questions were authored and labeled before requesting their embeddings, with two
questions for each of five regulatory topics. All methods used the same corpus and top-k of four;
vector paths used the existing cosine floor of 0.2. Results count document relevance, not legal
applicability. Multiple chunks from one document are allowed, matching production behavior.

This is a small authored regression suite, not a population-level estimate or proof that every
semantic result applies to a case. It excludes real/uploaded data and does not measure drafting
quality, production latency, or general out-of-domain abstention. The floor remains 0.2 because this
sample does not justify tuning it; expand independently labeled queries before calibration.

The corpus generation used **1,348 input tokens**, estimated **$0.000027** from the verified catalog
rate. The ten query fixtures used **165 input tokens**, estimated **$0.000003**. These are rounded
token-price estimates, not reconciled provider invoices. No SAR budget increase was required. Runtime
query embeddings receive a separate conservative **$0.05/day** shared reservation ceiling, at most
**$1.55 in a 31-day month**; reservations include possible retries and are never refunded. Exhaustion
selects lexical fallback. Public corpus refreshes remain explicit paid maintenance.

Evidence and reproduction:

- [Per-query rankings and metrics](semantic-rag-default.json).
- [Authored questions and labels](../../../tests/fixtures/rag_relevance.yaml).
- [Recorded public query vectors](../../../tests/fixtures/rag-query-embeddings.json).
- [Provider-free replay test](../../../tests/unit/test_rag_relevance.py).
- [Shared-budget concurrency/restart tests](../../../tests/integration/test_rag_query_budget.py).

Run `uv run pytest --no-cov -q tests/unit/test_rag_relevance.py` to rederive the published rankings
against the shipped corpus and actual recorded provider vectors without credentials or network.

Verification on 2026-10-26:

- `make pre-pr` passed: 2,427 backend tests (96.28% coverage), 487 frontend tests, and all remaining
  privacy, quality, documentation, source, benchmark, and Kubernetes validation gates.
- `make docker-build` passed. The semantic index stage ran with `--network=none`; the resulting
  non-root image returned `/healthz` and `/readyz` 200 and served lexical fallback without networking.
- A real source-free public query used semantic retrieval and ranked `31 CFR 1010.314` first.
  It consumed 21 embedding tokens; a repeated query consumed zero additional tokens. Its conservative
  $0.0000072 budget reservation persisted in local storage.
- Two concurrent PostgreSQL reservations of $0.03 under a $0.05 ceiling admitted exactly one request.
  The counter retained $0.03. The temporary local PostgreSQL container was removed after verification.

These are local implementation checks; they do not assert that the production URL was redeployed.

## Release extraction verification — 2026-10-28

The semantic retrieval changes are extracted onto `codex/semantic-regulatory-retrieval` from
`origin/release/0.7.0` at `d1fee9a38f043364a04523344a68ed16f80531c0`, preserving the merged
maker–checker and risk-evidence contracts. Recorded public vectors are reused; no paid embedding
refresh or new provider query was performed for this extraction.

| Extraction check | Result |
| --- | --- |
| `make pre-pr` | Complete canonical gate passed, using the local coverage filename workaround described below. |
| Focused snapshot, relevance, cache, budget and ingestion tests | 29 passed; recorded vectors reproduce the published rankings without network or credentials. |
| Portable backend suite | 2,427 passed; 26 skipped; 25 deselected; 96.35% branch-aware coverage. |
| Frontend suite | 489 passed across 78 files; 91.10% branch coverage; frontend sources unchanged. |
| Backend changed-line coverage | 98% versus the merged release base, including the new budget and snapshot modules. |
| Fresh migrated PostgreSQL | Canonical `make postgres-run-test`: 14 passed, including the new shared embedding-admission proof. Two independent $0.03 reservations admit exactly one; a restarted adapter reaches the $0.05 ceiling and rejects further spend. |
| `make docker-build` | Passed for `linux/amd64`; the semantic bake runs with `--network=none`, materializing 12 chunks from six public documents. |
| Network-isolated container | Non-root UID 10001; `/healthz` and `/readyz` both HTTP 200 with the semantic index required. The matching provider-space index is ready; missing budget storage selects four lexical results without a provider call. |
| Teardown | Extraction-only PostgreSQL and API containers removed; original local services preserved. |

The first local gate completed every backend test but its coverage writer failed because the
machine hostname was `/etc/localtime`, which coverage inserts into its parallel-data filename.
The repeat uses a temporary, out-of-repository coverage filename sanitizer through `PYTHONPATH`.
It changes only coverage output naming; system hostname, application behavior and coverage
thresholds are unchanged. The Docker build and container checks use the standard image.
Local logs, container evidence and the temporary runner workaround are gitignored under
`.local/verification/semantic-rag-release-070/` in the extracted checkout.
