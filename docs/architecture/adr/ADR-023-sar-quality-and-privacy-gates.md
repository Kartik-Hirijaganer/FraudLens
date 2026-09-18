# ADR-023 — SAR quality and privacy gates are deterministic CI contracts

- **Status:** Accepted
- **Date:** 2026-09-13
- **Related:** [ADR-019 — bounded multi-agent SAR drafting](ADR-019-multi-agent-sar-drafting.md)
  · [ADR-026 — provenance-derived synthetic-only model egress](ADR-026-synthetic-only-model-egress.md)

## Context

FraudLens already grounded SAR citations, applied deterministic claim-support checks, masked common
identifier patterns, and published a synthetic paired drafting study. Those mechanisms were spread
across runtime paths and tests, however, with no named CI target or committed acceptance thresholds.
An implementation could therefore weaken citation recall, unsupported-claim detection, or required
fact coverage without failing a focused quality contract.

The quality claims must remain reproducible without credentials, provider availability, network
access, or paid inference. They also need to exercise the production schemas and grounding behavior
rather than maintain a benchmark-only interpretation of citation or claim correctness.

### Options considered and rejected

1. **Rely on general unit coverage** — rejected because coverage shows execution, not that measured
   SAR quality remains above an explicit acceptance threshold.
2. **Call live models in CI** — rejected because outputs and cost are nondeterministic and would make
   a privacy gate depend on the external path it is meant to constrain.
3. **Keep metrics inside the SAR-study harness** — rejected because runtime quality tests, future
   vLLM benchmarks, and publication validation would then risk diverging definitions.
4. **Use only schema and object-level egress assertions** — rejected because adapters serialize,
   retry, and reroute requests after those assertions. Raw request-body capture closes that gap.
5. **Treat a passing synthetic suite as production validation** — rejected because the fixture
   distribution cannot establish safety or regulatory correctness for real cases.

## Decision

FraudLens treats citation quality, hallucination detection, published-study consistency, and model
egress as a named deterministic CI contract: `make quality-gates` runs tests marked `quality` under
`tests/quality/`, performs no provider call, and is a dependency of `make ci` and the reusable CI
quality job.

The reusable metric functions live in `fraudlens_ml.evaluation` and return frozen Pydantic results.
The committed `config/quality.yaml` owns the minimum citation precision and recall, unsupported-claim
recall, clean-draft false-positive ceiling, and required-fact coverage. The test corpus covers all 32
frozen synthetic SAR-evaluation scenarios through the mock drafter and production grounding parser,
adversarial unsupported claims, transport-captured retries and fallbacks, and recomputation of the
published study's per-scenario aggregates and judge evidence.

These gates evaluate deterministic behavior on synthetic fixtures. They do not certify regulatory
filing accuracy, anonymisation, provider compliance, or performance on real customer data. Human SAR
approval remains unchanged and no quality result can transition an alert or authorize a filing.

### Why

**1 · Named thresholds make quality regression reviewable.** A maintainer can identify the exact
metric, fixture, and configured limit that failed instead of inferring quality from a general test
suite.

**2 · Provider-free evidence is stable.** CI outcomes do not depend on model drift, credentials,
rate limits, external availability, or spend. A stricter temporary threshold test proves that the
gate rejects an intentionally weakened fixture rather than merely executing code.

**3 · Shared metrics prevent report semantics from forking.** Citation precision/recall,
unsupported-claim recall, false-positive rate, and fact coverage have one typed implementation that
later benchmarks can reuse.

**4 · Transport inspection tests the last responsible boundary.** Capturing serialized
OpenAI-compatible request bytes exposes leaks that object-level tests could miss and verifies both
retry and governed fallback behavior under a socket-denial guard.

## Tradeoffs accepted

- The suite adds deterministic fixtures and recomputation logic that must evolve with deliberate SAR
  contract changes. This maintenance cost is accepted in exchange for explicit regression evidence.
- Rule-based unsupported-claim fixtures approximate hallucination behavior; they do not measure every
  semantic contradiction. Their scope and false-positive ceiling remain visible rather than being
  overstated.
- Published-study validation protects the committed artifact's internal consistency, including its
  negative findings, but does not rerun paid generations or independently reproduce the model judge.
- Thresholds are repository policy. Tightening them may require stronger fixtures or implementation
  behavior; weakening them is a reviewable configuration change rather than a hidden test edit.

## Reconsider when

- A representative, legally approved non-synthetic evaluation corpus exists with a reviewed privacy
  and retention protocol; add a separate protected gate rather than relabeling this synthetic one.
- Semantic contradiction detection can be made deterministic and demonstrably improves recall
  without exceeding the clean-draft false-positive ceiling.
- Benchmark consumers require additional metrics; extend the shared Pydantic result contracts and
  version thresholds rather than calculating incompatible report-only values.
- CI runtime becomes material; preserve the named acceptance contract while sharding provider-free
  suites or caching immutable corpus preparation.

**Never** weaken these checks to make a generated result look favorable, call a provider from the CI
gate, or interpret a passing synthetic fixture suite as permission to process real customer data.
