# ADR-017 — Graph-feature serving boundary: GFP measured offline, never served

- **Status:** Accepted
- **Date:** 2026-07-14
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when (per master-plan §22)
- **Related:** [ADR-015 — tenant-safe global model training](../../../plans/2026-06-12-aml-fraud-detection-system.md#22-decision-records-adrs)
  · study plan [`plans/2026-07-14-gfp-isolation-gap-visual-study.md`](../../../plans/2026-07-14-gfp-isolation-gap-visual-study.md)
  · [Snap ML `GraphFeaturePreprocessor` API](https://snapml.readthedocs.io/en/latest/graph_preprocessor.html)
  · [IBM AML graph-feature study](https://arxiv.org/html/2402.08593)

## Context

FraudLens serves a 19-feature model (`FEATURE_NAMES` in
`packages/fraudlens-ml/src/fraudlens_ml/scoring/features.py`) whose destination aggregates are
single-hop and tenant-scoped. IBM Snap ML's `GraphFeaturePreprocessor` (GFP) can append
vertex-statistic and multi-hop graph-pattern features (fan, scatter-gather, temporal/simple
cycles), but it requires a timestamp-ordered edge list with unique edge/source/destination
identifiers and keeps a long-lived in-memory transaction graph. Those identifiers are
**deliberately absent** from the live `RuleContext` (`fraudlens_core`). The GFP study measures,
offline, (1) how much detection value GFP features add over the current 19, and (2) how much of
that lift changes when the graph is restricted to the transaction-owner tenant.

## Decision

GFP graph features are **measured offline only** and **never enter live scoring** — in *either*
scope:

- All graph/benchmark code lives in **`scripts/lib/gfp/`**, physically outside `fraudlens_ml`,
  `fraudlens_core`, and `backend`; runtime packages never import it, and `snapml` lives only in
  the root benchmark-only `gfp` dependency group (never in default sync, CI installs, or the
  deploy image).
- The served feature contract is unchanged: the scored vector stays **exactly the 19
  `FEATURE_NAMES`**; no `gfp_*` feature name, node id, edge id, or graph-derived value reaches
  `RuleContext`, the feature builder, the scorer, or any persisted tenant row.
- The study's results ship as a committed, synthetic, aggregated research artifact behind the
  authenticated research page — a static deliverable, **not** a graph-serving path.

## Why — tenant confidentiality is the spine

A live GFP graph is an accumulating cross-transaction state. Fed globally, **Agency A's risk
score would depend on Agency B's transaction topology**: a cycle or scatter-gather pattern
completed by another tenant's edges changes A's feature values. That violates FraudLens's
tenant-isolation invariant — every tenant-scoped DB read and background job binds `agency_id`,
and no request-time computation may mix tenants' data. Secondary benefit: the same
identifier-free design preserves the **PHI-safe boundary** — GFP needs stable account/edge
identifiers, and `RuleContext` deliberately carries none, so in a real deployment raw account
tokens never enter a long-lived in-process structure or its logs/artifacts. (The study data is
public and synthetic; PHI is the secondary rationale, not the driver.)

## Options considered and rejected

1. **Process-global online GFP graph** — rejected: makes one tenant's score a function of other
   tenants' topology (the confidentiality breach above); also unbounded memory growth keyed by
   raw identifiers.
2. **Node identifiers in `RuleContext`** — rejected: reintroduces stable account identifiers
   into the live scoring path, breaking the identifier-free (PHI-safe) contract for a feature
   set with unproven serving value.
3. **Persisting globally-derived graph values on tenant rows** — rejected: launders cross-tenant
   information into tenant-scoped storage; the value is still a function of other agencies'
   edges even if the graph itself never serves.
4. **Request-local partial graph rebuild** — rejected: a per-request rebuild sees only a
   fragment of the topology, so the features are silently different from the benchmarked ones
   (wrong numbers), at significant latency/IO cost.
5. **Reading ADR-015's global-training allowance as authorization for cross-tenant online
   reads** — rejected: ADR-015 permits one *offline-trained* global model under a strict
   tenant-safety policy (no PHI/raw IDs/`agency_id` features, immutable manifests, per-tenant
   eval slices). It does not authorize *online* computation over other tenants' transactions.

## Per-tenant serving — possible, but deferred

A per-tenant GFP graph (only the owner agency's edges) would not breach confidentiality, and the
study measures exactly what that restriction costs (the **signed isolation delta**). It is still
**deferred** because serving it requires, at minimum:

- an agency-bound edge contract (stable per-tenant edge/node identifiers with authZ),
- strictly ordered ingestion into the graph state,
- replay/checkpoint of graph state across restarts,
- concurrency and eviction policies for long-lived per-tenant graphs,
- a cold-start story (features are wrong until the graph warms),
- model/graph version parity (a model trained on GFP features is only valid against the same
  graph configuration and warm-up state), **and**
- a **positive measured benefit** that justifies all of the above.

**Tradeoffs accepted:** any multi-hop detection lift the study measures — global *or*
per-tenant — is deliberately left unserved for now; the demo's research page makes that
trade-off visible instead of hiding it.

**Reconsider when:** a new ADR **plus a security review** revisits this boundary — never by
reinterpreting this one. Triggers: the published study shows a material, positive per-tenant
lift on the metrics frozen below, and the operational preconditions above are designed and
costed.

## Frozen benchmark protocol (freeze before any holdout inspection)

The study's **dataset, sampling, temporal/leakage, tenant-ownership, arms/feature-naming,
metrics, and curation contracts** are the "Benchmark contract" section of
[`plans/2026-07-14-gfp-isolation-gap-visual-study.md`](../../../plans/2026-07-14-gfp-isolation-gap-visual-study.md).
They are **frozen as of this ADR's acceptance (2026-07-14), before any new holdout result is
inspected**, and are pinned in machine-validated form in `config/gfp-benchmark.yaml`
(`GfpBenchmarkConfig`, `scripts/lib/gfp/`). If the isolation delta comes back zero or negative,
that is a **valid result**: only explanatory copy may change — never the protocol, the metrics,
or this ADR's separate operational rationale. Any post-hoc change to folds, sampling, features,
or hyperparameters after seeing holdout results invalidates the run.
