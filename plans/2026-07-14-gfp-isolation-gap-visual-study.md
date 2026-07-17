# GFP typology lift, tenant-isolation gap, and visual research demo

> **Status:** Authoritative merge of the two predecessors —
> [`2026-07-13-offline-gfp-benchmark.md`](2026-07-13-offline-gfp-benchmark.md) and
> [`2026-07-14-gfp-tenant-cost-visual-demo.md`](2026-07-14-gfp-tenant-cost-visual-demo.md) — plus the
> reviewer draft. Keep both predecessors for review until this is accepted; implementation +
> drift-check use only this file. **No commit or push without explicit human permission.**

## Context — two questions, one visible trade-off

FraudLens serves a 19-feature model whose destination aggregates are single-hop and tenant-scoped;
it detects no multi-hop fan / scatter-gather / cyclic laundering topology. IBM Snap ML's
`GraphFeaturePreprocessor` (GFP) takes a timestamp-ordered edge list, keeps an in-memory graph, and
appends vertex-statistic + graph-pattern features. It requires unique edge/source/destination
identifiers — deliberately absent from the live `RuleContext`
([GFP API](https://snapml.readthedocs.io/en/latest/graph_preprocessor.html),
[IBM AML study](https://arxiv.org/html/2402.08593)).

This study answers **two separate questions:**
1. How much detection value do GFP features add over the current 19?
2. How much of that lift **changes when the graph is restricted to the transaction-owner tenant**,
   matching FraudLens's data boundary?

**Primary deliverable:** an authenticated, interactive research page that makes the trade-off
*visible* — driven by a committed, synthetic, offline study artifact, **not** a graph-serving path.
**ADR-017** records why neither the global nor the per-tenant GFP features enter live scoring.

Portfolio arc: log into two isolated demo agencies → *feel* the isolation → open the research page →
watch a cross-tenant laundering cycle's edges **vanish** when you switch from the global view to an
agency view → read one honest number (the signed isolation delta) and the ADR link.

## Decisions (merged; my final say noted)

- **Constraint = tenant confidentiality** (Agency A's score must not depend on Agency B's topology).
  PHI is secondary (data is synthetic) but the same identifier-free design also preserves the
  PHI-safe boundary in a real deployment.
- **Keep multi-tenant.** Dropping it → a generic Kaggle model. The isolation + deliberate deferral
  is the non-generic part.
- **Signed, not assumed.** Report `isolationDelta = global − perTenant`; only call it "cost of
  isolation" when the delta is **positive**, else "isolation delta" with the observed direction.
  (Adopted from the reviewer draft — more honest than my "cost of isolation gap.")
- **Topology-preserving sampling.** Full HI-Small; HI-Medium/LI-Medium via **node-induced**
  subgraphs, never random row sampling (which destroys cycles/paths). *This fixes a real bug in the
  lean predecessor.*
- **Graph code lives in `scripts/lib/gfp/`, not the runtime package.** Reversing my earlier call:
  physically isolating it from `fraudlens_ml`/backend reinforces the ADR thesis (never serves) and
  keeps snapml out of the shipped package. Covered by a dedicated `make gfp-reference-test` ≥90%
  gate (scripts are outside the default coverage source).
- **Right-sized.** In-memory on node-induced samples; **no** out-of-core trainer, mmap
  materialization, resumable cache, scheduled/Azure job, or CI file-size gate. ≤500 LOC/file by
  discipline. snapml runs once (native x86-64, or a throwaway `docker run` on the arm64 Mac); pure
  reference engine is the oracle + portable fallback; a fake isolates orchestration tests.
- **Serving unchanged.** GFP features never enter `features.py`/`FEATURE_NAMES`/the scorer; the
  existing per-row anti-skew suite stays green; the served vector stays exactly 19 values.
- **My trims of the draft:** no hard 64-GiB requirement (document a memory budget; node-sample
  HI-Small too if constrained); snapml pin verified at impl time (floor `>=1.15`, pin exact once the
  wheel is confirmed) rather than asserting `1.17.2` up front.

## Benchmark contract

**Datasets & sampling**

| Dataset | Graph context | Targets | Purpose |
|---|---|---|---|
| HI-Small | every servable row | all | primary full-data result |
| HI-Medium | node-induced subgraph | ≤1,000,000 stratified | scale / base-rate replication |
| LI-Medium | node-induced subgraph | ≤1,000,000 stratified | illicit-ratio comparison (HI≈0.1% vs LI≈0.05%) |

- **Node-induced selection (label-blind):** keep a node when `SHA-256(normalized (bank,account))`
  falls in the configured hash fraction (start ¼ ≈ 1/16 of edges); keep an edge when *both*
  endpoints are kept. Compute features on the **full retained context**, then stratify targets
  (600k/200k/200k) with `sample_frame` semantics, matching each source fold's illicit ratio.
  Context-only edges influence features but are never training examples. If a fold lacks a class,
  escalate the fraction ¼→⅓→½, then fail (never silently resize). Manifest records fraction,
  context/target counts, class counts, ratios.

**Temporal / leakage**
- `servable_frame` normalization; never filter on the label. Stable `originalRowId`; order by
  `(timestamp, originalRowId)`. Freeze strict chronological 60/20/20 folds **before** any GFP call,
  advancing boundaries through a whole equal-timestamp cohort so no timestamp spans folds.
- Identical target IDs, fold IDs, labels, base features, seed (1729), calibration, and XGBoost
  params across every scope and arm.
- Feed GFP in 128-edge batches that never cross a fold boundary; graph state flows
  train→cal→holdout, never backward or across datasets/scopes/tenants. Labels never enter GFP.
- Disclose: GFP's per-batch transform is paper-aligned batch-causal, **not** strict row-at-a-time
  serving parity; the existing anti-skew evidence covers **only Arm A's 19 served features**. Run a
  batch-1 vs batch-128 sensitivity check on a fixed small graph.

**Tenant ownership (reuse the existing demo partition — don't invent a second model)**
- Node owner = `demo_agency_index(bank, len(AML_DEMO_AGENCIES))`; edge owned by its **source**
  node's agency (matches `map_ibm_demo_row` + the ingestion repo selection).
- **Global** graph = all context edges. **Per-tenant** graph for agency N = only edges owned by N
  (counterparty destination nodes still appear as endpoints; other agencies' edges do not).
- Concatenate per-tenant features from all agencies back into original row order.
- Train **one pooled per-tenant-scope model** (not three agency models) so the experimental variable
  is *graph visibility*, not sample size. **Arm A is trained once** and reused in both scope tables;
  any Arm-A difference across scopes is an invariant violation that aborts the run.

**Arms & canonical feature names**
- **A** = current 19 `FEATURE_NAMES`. **B** = A + GFP fan/degree histograms + vertex statistics.
  **C** = B + scatter-gather + temporal-cycle + length-constrained simple-cycle histograms.
- Pin GFP in `config/gfp-benchmark.yaml`: seed 1729, batch 128; edge cols
  `[edge_id, dense_src, dense_dst, utc_epoch_s, usd_amount]` (finite, `<2^53`); windows 86,400 s
  (global/fan/degree/vertex/temporal/simple), 21,600 s (scatter-gather); all six pattern/stat
  families on; simple-cycle max length 10; bins `2..30` (fan/degree/scatter-gather/temporal),
  `2..10` (simple-cycle); vertex stats {fan,degree,ratio,avg,sum,var,skew,kurtosis} over
  {source-out, source-in, target-out, target-in} on timestamp + USD.
- Names generated **from the validated config** (no hand-maintained parallel lists): histograms
  `gfp_<pattern>_ge_<lo>[_lt_<hi>]`, vertex `gfp_<endpoint>_<dir>_<rawcol>_<stat>`. Export
  `GRAPH_ARM_B_FEATURE_NAMES`, `GRAPH_ARM_C_INCREMENT_FEATURE_NAMES`, `GRAPH_FEATURE_NAMES = B+C`;
  assert `set(GRAPH_FEATURE_NAMES).isdisjoint(FEATURE_NAMES)`.

**Metrics & interpretation** (per dataset × arm × applicable scope)
- Holdout positives/negatives/illicit ratio; raw PR-AUC + normalized mean-lift `PR-AUC/ratio`;
  ROC-AUC (secondary); Brier + ECE; precision/recall/captured-positives at top 0.1/0.5/1%;
  calibration-selected minority F1 (threshold from calibration, applied once to holdout);
  A→B, B→C (incremental multi-hop), A→C deltas.
- Isolation (signed): `isolationDelta = globalMetric − perTenantMetric` (B, C);
  `lostGraphLift = (C_global−A) − (C_perTenant−A)`;
  `retainedGraphLift = (C_perTenant−A)/(C_global−A)` only when global lift > 0, else `null` + note.
- Paired 95% interval on PR-AUC deltas: 200 deterministic stratified bootstrap replicates on a fixed
  ≤250k holdout subset, same sampled IDs across compared arms/scopes; record the subset caveat.

## Phases

**Phase 1 — ADR-017 + freeze the protocol**
- [ ] `docs/architecture/adr/ADR-017-graph-feature-serving-boundary.md` (Accepted, 2026-07-14) +
      `adr/README.md` index + pointers from ARCHITECTURE governance and master-plan §22. Spine:
      tenant confidentiality (global online graph makes an Agency-A score depend on Agency-B
      topology; tenant reads/jobs bind `agency_id`); PHI-safe boundary as a secondary benefit.
      Reject: process-global graph; node IDs in `RuleContext`; persisting globally-derived values on
      tenant rows; request-local partial rebuild; reading ADR-015's global-training allowance as
      authorization for cross-tenant online reads. Per-tenant serving = possible but deferred (needs
      an agency-bound edge contract, ordered ingestion, replay/checkpoint, concurrency/eviction,
      cold-start, model/graph version parity, **and** a positive measured benefit); reconsider only
      via a new ADR + security review.
- [ ] Freeze the dataset/sampling/temporal/tenant/feature/metric/curation contracts above before
      inspecting any new holdout result.

**Phase 2 — Benchmark-only deps + dataset variants**
- [ ] Root `gfp` dependency group with the snapml pin + an x86-64 platform marker; mypy `snapml.*`
      override. **Not** in `fraudlens-ml`/backend/default sync/deploy images (no arm64-mac wheel).
- [ ] Typed fetch-registry entries `ibm-aml-hi-medium`, `ibm-aml-li-medium` (preserve `ibm-aml`);
      `make fetch-gfp-data` (Infisical-backed Kaggle, one file at a time; never auto-download from
      the benchmark). PHI-free provenance (filename, sha256, counts, ratio, time range).
- [ ] `GfpBenchmarkConfig` (frozen, `extra="forbid"`, described fields) rejecting bad
      windows/bins/fractions/quotas/paths/engine-version.
- [ ] `make gfp-container CMD=...` — throwaway pinned Python-3.11 x86-64 image for arm64 hosts;
      datasets read-only, only repo/`.local` writable. A local compat wrapper, **not** a deployable
      image or CI job.

**Phase 3 — Deterministic edges, samples, folds, scopes** (`scripts/lib/gfp/`, ≤500 LOC/module)
- [ ] Frozen Pydantic boundaries: `DatasetStudySpec`, `GraphFeatureConfig`, `DatasetProvenance`,
      `FoldAssignment`, `GraphFeatureSchema`, `ArmMetrics`, `ScopeComparison`, `StudyReport`, curated
      graph records.
- [ ] IBM rows → typed edges via `ibm_account_key`: stable dense node IDs + edge IDs, UTC seconds,
      USD-normalized amounts (reject unknown currencies — don't silently treat as USD), source/dest
      agency, label, fold, target/context flag. Assert uniqueness, finiteness, `<2^53`, 1:1 row
      alignment.
- [ ] Label-blind node-induced context selection + post-feature stratification per the contract;
      stream medium CSV selection in chunks (no full medium frame / mmap / resumable cache). Strict
      timestamp-cohort folds **without** touching production's account-grouped `split_chronological`.
- [ ] Global + three agency-owned edge streams; validate concatenation restores each target exactly
      once and no other-agency edge enters a tenant stream.

**Phase 4 — Reference engine, snapml adapter, fake**
- [ ] Typed `GraphPreprocessor` protocol (explicit arrays + schema; never mutate caller arrays).
- [ ] Pure reference engine (immutable adjacency/time-window indexes; pure fan/degree/vertex/
      scatter-gather/temporal-cycle/simple-cycle functions) — clarity + small/medium smoke, not
      full-data performance. Fake = deterministic schema-correct transformer that must **not** pass
      the known-answer graph tests.
- [ ] Single lazy `snapml` import in one adapter: one instance per scope/tenant; copy inputs;
      validate output width/order; realign by edge ID; keep only engineered columns as `float32`;
      drop all identifiers. Materialize all GFP groups once per scope, project B/C from it; run
      scopes sequentially, releasing arrays/models between them (documented memory budget).
- [ ] `make gfp-test` (real adapter, x86-64) + `make gfp-reference-test` (portable, `--cov=scripts/
      lib/gfp --cov-fail-under=90`). Published reports must say `engine=snapml`; reference reports
      cannot be promoted to committed results.

**Phase 5 — Paired A/B/C training + report**
- [ ] Pure orchestrator + thin `scripts/benchmark_gfp.py` CLI; refuse `environment=prod`, DB
      connections, registry writes, activation, artifact dirs. Build Arm A via `build_feature_matrix`
      (preserve ordering + anti-skew semantics); apply frozen target/fold indices → `DataSplit`.
- [ ] Train A once + B/C per scope with public `train_candidate`; recover calibrated probs from its
      booster/calibration; **don't** duplicate private XGBoost params or tune on holdout. Validate
      identical target/fold/label hashes before each fit; any Arm-A scope discrepancy aborts.
- [ ] Local output `.local/gfp-study/<run-id>/study.json` (lib versions, config/dataset hashes,
      sample/fold fingerprints, feature names, metrics, intervals, `servingEligible=false`; **no**
      models/predictions/IDs/paths). `make gfp-benchmark` (three-dataset snapml run) +
      `make gfp-publish GFP_RUN=...` (validate completeness/redaction, then atomically write
      `docs/reference/benchmarks/gfp-tenant-isolation-study.{json,md}`). Markdown rendered solely
      from the typed JSON; positive resume wording only when the interval/lift supports it, else
      neutral factual wording.

**Phase 6 — Deterministic curation for the visual**
- [ ] Select exactly three motifs from the global HI-Small context: a scatter-gather, an
      intra-tenant cycle, and a **cross-tenant** cycle (length 3–10). Don't invent one if none
      exists (publication fails if no cross-tenant cycle spanning ≥2 agencies).
- [ ] Deterministic ranking: contains ≥1 public illicit edge → largest global-vs-tenant feature
      delta → fewest nodes (≤12) → earliest timestamp → stable edge-ID hash. `servable=true` only
      when every displayed-pattern edge is owned by one tenant.
- [ ] Emit `frontend/src/data/gfp-tenant-isolation-study.json` (opaque `node-01`/`edge-01`, relative
      time offsets, amount **bands**, agency index/name, typology, edge owner, servability; embed the
      report SHA-256). Never commit raw tokens/amounts. Validate the two committed artifacts can't
      drift.

**Phase 7 — Two-tenant demo identity + research page** (`frontend`)
- [ ] Add one Agency-Two analyst demo identity (existing public synthetic password, agency-bound
      Supabase JWT). Refactor the persona picker from role-only to typed personas (fix the
      `demoRoleByRole` mis-selection); resolve sessions via verified `/me`, persist `agencyId`; show
      Agency Two only when live demo auth is on; dev bypass stays Agency One. *(Verify current auth
      wiring first.)* No production bypass, no client-selectable agency header.
- [ ] Authenticated route `#/research/graph-typologies` + a `Research` sidebar group (any role with
      `view`). No backend call, no cross-tenant query.
- [ ] `d3-force@3.0.0` + `@types/d3-force`; small **SVG** node-link; deterministic layout (clone
      nodes, init coords from stable IDs, stop the sim, run a named 300-tick layout, return immutable
      positioned nodes; D3 mutation stays in the adapter).
- [ ] Three motif tabs + Global / Current-agency segmented control. Global shows all edges; tenant
      view keeps owned edges solid and renders unavailable other-agency edges as **labelled ghosts**
      so the lost topology is legible. Default the tenant control to the verified demo agency.
- [ ] Wise tokens only ([`DESIGN.md`](DESIGN.md)): neutral/white cards, `rounded-xl`; agency colors
      = ink / `accent-cyan` / `accent-orange` (never primary green); letter labels + legend so color
      isn't the sole channel. Show raw PR-AUC, normalized lift, A→C lift, and the **signed** isolation
      delta by the hero (copy says "isolation delta," not "cost," unless positive); a prominent
      "public synthetic offline study — not live tenant data" banner + ADR-017 link.
- [ ] a11y/responsive: SVG title/desc, keyboard-operable controls, visible focus, non-hover detail
      panel, text alternative listing nodes/edges, reduced-motion static layout, mobile stacking.

**Phase 8 — Tests**
- [ ] Sampling/folds: deterministic hashes; label-blind context; exact target quotas; fold
      base-rate tolerance; ties; insufficient-class escalation/failure; unchanged source frames; no
      row loss/dup. Reference known-answer: fan/degree/vertex, scatter-gather, in/out-of-window
      temporal cycle, simple cycle ≤10 and >10, self-loop, repeated edge, equal timestamp, empty,
      one edge.
- [ ] snapml parity (batch 1 & 128): names/order, alignment, finiteness, per-feature `assert_allclose`
      (documented tolerance); `make gfp-test` fails if snapml absent; default suite uses reference/
      fake. Tenant: owner matches `map_ibm_demo_row`; global ⊇ all context; tenant ⊆ owned; intra-
      tenant cycle survives; cross-tenant cycle disappears from every single-agency graph; no state
      leak between runs.
- [ ] Benchmark (fake): A trained once; identical hashes; all A/B/C × scope comparisons;
      signed/negative/zero deltas; null retained-lift denominator; normalization; calibration-only
      threshold; deterministic paired intervals; incomplete run can't publish.
- [ ] Serving guards: `GRAPH_FEATURE_NAMES` disjoint from `FEATURE_NAMES`; served width == 19;
      `RuleContext` field set unchanged; benchmark paths can't reach `save_artifact`/activation/
      repos/DB sessions; all `test_aml_fraud.py` anti-skew cases pass.
- [ ] Curation/redaction: three motifs; deterministic selection; correct servability; report-hash
      match; no raw tokens/paths/secrets/PHI/labels/per-row predictions in committed artifacts.
- [ ] Frontend: parser rejects malformed/drifted data; route requires a session; both agency
      personas resolve; tab/scope switching; cross-tenant edges ghost in tenant view; agency
      text/legend present; metrics + ADR link render; missing data fails the build (no placeholders).
      Dedicated `scripts/lib/gfp` branch coverage ≥90% via `make gfp-reference-test`. All new/changed
      files ≤500 lines.

**Phase 9 — Execute, document, gate**
- [ ] Run reference/fake locally; run real-adapter parity + the full benchmark in the x86-64 wrapper
      (or a native x86-64 Python-3.11 host with adequate RAM/disk — document the budget; node-sample
      HI-Small if constrained). Record aggregate resource use without host/user/path data.
- [ ] Inspect only completeness/invariants/redaction/uncertainty — never change folds/sampling/
      features/hyperparameters after seeing holdout. A negative/null delta is a valid result.
- [ ] Publish report + curated frontend JSON; build the app; manually verify both demo agency logins
      default to their tenant perspective on the same clearly-labelled offline page.
- [ ] Update architecture + model-lifecycle docs (offline-only flow, ADR-017, reproduction commands,
      dataset/sample table, limitations; don't imply the reference engine or visual is a serving
      component). Run `make docs`, `make gfp-reference-test`, `make gfp-test` (x86-64),
      `pytest -q tests/unit/test_aml_fraud.py --no-cov`, `make pre-pr`, and
      `drift-check plans/2026-07-14-gfp-isolation-gap-visual-study.md all`. Resolve every
      critical/high finding.

## Contracts & deliverables
| Contract / artifact | Decision |
|---|---|
| Serving feature contract | Unchanged 19-feature v2; no GFP names or IDs |
| Graph code location | `scripts/lib/gfp/` only; no runtime-package graph module |
| Published engine | IBM Snap ML (pinned) on Python 3.11 x86-64; reference/fake for CI |
| Benchmark command | Local, explicit, non-production; no DB/registry writes |
| Result report | `docs/reference/benchmarks/gfp-tenant-isolation-study.{json,md}` |
| Visual data | `frontend/src/data/gfp-tenant-isolation-study.json`, build-time static |
| Visual route | `#/research/graph-typologies`, authenticated shell, `d3-force` SVG |
| Serving decision | ADR-017: measure offline, serve neither scope |

## Non-goals & risks
- No graph-feature serving, graph DB, cross-tenant API/endpoint, or persisted graph-derived feature.
- No full-medium training, full-medium GFP graph, out-of-core matrix, resumable cache, scheduled/
  Azure job, or benchmark deployment. Deploying the *app* to Azure is a separate follow-up.
- Bank-hash agency ownership is the existing deterministic synthetic demo partition, documented as an
  experimental proxy — **not** a claim about real legal ownership.
- Node-induced medium samples preserve retained topology but omit paths crossing discarded nodes →
  a downward bias on graph-pattern counts; report it.
- A static artifact behind login is **not** confidentiality authorization; it's safe only because it
  is public-synthetic, aggregated, and opaque — tests and copy keep that explicit.
- GFP batch semantics ≠ strict per-row train/serve parity; existing anti-skew evidence covers only
  Arm A's 19 served features.
- If the isolation delta is zero or negative, keep the result and revise only the explanatory
  copy — never the protocol, metrics, or the ADR's separate operational rationale.

## Verification (end-to-end)
1. `make gfp-reference-test` + `pytest -k "gfp or benchmark"` + `test_aml_fraud.py` green; guard proves
   served width 19 and disjoint names.
2. `make gfp-benchmark` → `make gfp-publish` → `gfp-tenant-isolation-study.{json,md}` with A/B/C,
   B→C, HI-vs-LI, and the **signed** global−per-tenant delta; curated `…study.json` emitted with a
   matching report hash.
3. Local app → log into Agency One and Agency Two → open `#/research/graph-typologies` → the
   cross-tenant cycle's edges ghost out when switching to a single-agency view; metrics + ADR link
   render.
4. `make pre-pr` green; `drift-check … all` clean.
