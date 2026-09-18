# ADR-026 — Model egress is synthetic-only and derived from persisted provenance

- **Status:** Accepted
- **Date:** 2026-09-13
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** [ADR-014 — masked/hashed PHI storage](README.md)
  · [ADR-019 — bounded multi-agent SAR drafting](ADR-019-multi-agent-sar-drafting.md)
  · [ADR-023 — deterministic SAR quality gates](ADR-023-sar-quality-and-privacy-gates.md)
  · implementation plan
  `plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` (retired; see [retired-plans.md](../retired-plans.md#retired-plans))

## Context

Deterministic masking recognizes known identifier shapes, but it cannot prove that arbitrary free
text is anonymous. The pre-existing `SarInput` included tenant and database identifiers, caller- and
rule-originated text, and a pre-rendered RAG block. Passing that broad internal object to prompts made
safe egress depend primarily on detection after data had already entered the outbound construction
path.

FraudLens currently operates only synthetic cases. Its API, importers, portfolio demo, single-writer
drafter, multi-agent graph, tool resubmission, retry, fallback, and benchmark paths must enforce that
claim from trusted persisted state, not from a caller-supplied classification or a provider setting.

## Decision

Every live SAR model request is built from a frozen `SarModelInput` with `extra="forbid"`. The model
schema contains only case-scoped aliases; verified amount, currency, country, controlled channel,
direction, and occurrence time; numeric backend aggregates; risk band and probability; controlled
AML rule types with policy-owned reason templates; numeric SHAP drivers in `FEATURE_NAMES`; exact
public regulation excerpts whose citation, metadata, and escaped-snippet digest match the committed
corpus; and explicit unknown markers.

Eligibility comes from the immutable `transactions.source` column. Every production ingest/import
path records one source; migration `0008_transaction_source` backfills recognizable IBM metadata and
marks every other legacy row `unknown`. `config/llm/egress.yml` maps persisted sources to data classes
and permits only approved synthetic sources. `api-upload` and `unknown` fail closed. `SarInput` has no
caller-authorizable data-class field.

The same projection is applied before single-writer or agent provider access. Agent tool results are
field-allowlisted, evidence identifiers become case-scoped digest aliases, and model output is masked
again before model-to-model resubmission. Retry and fallback reuse the already-projected messages.
Regulation digest mismatch or forbidden outbound content blocks before transport with a stable safe
code. A disallowed source returns `egress_source_not_allowed`; deterministic analysis can still
complete with a failed live SAR, and the provider-free mock remains available.

Cache identity includes tenant, exact projected evidence, prompt hash, requested model, and generation
settings. Application logs retain safe telemetry and never raw prompt or response bodies. This
decision does not broaden tenant access, allow real PHI, change human review authority, or authorize
any cloud/provider spend.

## Why

**1 · Positive selection is stronger than text detection.** Fields outside the closed schema cannot
enter a request, even when they do not resemble a known identifier pattern.

**2 · Persisted provenance cannot be self-declared at request time.** The repository owns source
classification at ingest, so changing a payload field or LLM data-class parameter cannot authorize
egress.

**3 · Corpus digests bind regulatory evidence.** Citation labels alone are insufficient: exact
escaped snippet content and committed metadata must match what the application ingested.

**4 · One boundary covers routing behavior.** Projection before provider selection means retries and
fallbacks cannot reconstruct a broader request, while agent tool/model resubmission gets an explicit
second sanitation step.

**5 · Stable refusal preserves deterministic value.** Blocking only the live drafting edge avoids
turning a privacy refusal into loss of rules, scoring, SHAP, or alert evidence.

## Options considered and rejected

1. **Continue relying on deterministic masking** — rejected because arbitrary names, notes, labels,
   and identifiers can evade pattern recognition while still being inappropriate to send.
2. **Trust the caller to declare `synthetic`** — rejected because request-controlled classification
   would make the allowlist an assertion rather than an authorization boundary.
3. **Allow all masked API uploads** — rejected because masking is not anonymisation proof and upload
   provenance provides no basis to claim synthetic content.
4. **Validate citation id without snippet content** — rejected because a valid label could accompany
   altered or injected text. Digest and metadata binding are required together.
5. **Apply the projection only to the single writer** — rejected because agents, tool results,
   revisions, retries, fallbacks, and benchmarks are separate serialization opportunities.
6. **Fail the whole investigation on egress refusal** — rejected because deterministic evidence is
   still valid and must remain available for human review.

## Tradeoffs accepted

- Some useful context is deliberately unavailable to models, including identifiers, raw RAG blocks,
  free-text notes, edited narratives, dataset labels, and arbitrary rule explanations. Human review
  and deterministic evidence remain the authority.
- Legacy rows without provable provenance cannot use live drafting. Re-ingestion through an approved
  source or the mock path is required; convenience does not override fail-closed classification.
- Corpus verification rebuilds a small deterministic digest allowlist. If the corpus grows materially,
  it may need a versioned cached index while preserving exact-byte semantics.
- Deterministic pattern checks and optional Presidio remain defense-in-depth. Neither is represented
  as proof that selected data is anonymous.
- Case-scoped digest aliases improve egress safety but are not durable database identifiers. Internal
  persistence retains authoritative ids behind the boundary.

## Reconsider when

- Real customer data is proposed for any model. Require a new privacy/compliance ADR, provider
  contracts, retention and regional controls, representative evaluation, and explicit human approval
  before adding a new allowed data class or source.
- A new ingest source can supply cryptographically or operationally verifiable synthetic provenance;
  add it through migration, policy, transport tests, and threat-model review together.
- Regulation corpus size makes per-request digest construction material; introduce a version-bound
  immutable cache without accepting citation labels or mutable external text on trust.
- A new agent/tool/model route is added. It must consume `SarModelInput` or a narrower typed projection
  and appear in the byte-level egress corpus before deployment.
- Required model quality cannot be achieved without an excluded field. Evaluate a narrowly derived,
  non-identifying replacement rather than passing the raw field.

**Never** add `api-upload`, `unknown`, or a real-data source to the allowlist merely to unblock a
request, and never let a caller-supplied classification authorize model egress.
