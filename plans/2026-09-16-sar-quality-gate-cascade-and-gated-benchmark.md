# Release 0.5.0 — SARQualityGate, AWQ→BF16→external cascade, and gated-architecture re-benchmark

## Context

Two resume bullets need to be fully defensible:

> Built a Python AML pipeline using LangGraph, XGBoost/SHAP, and ChromaDB for citation-grounded SAR drafts; deployed on Azure AKS with Terraform/HPA and added citation-quality and hallucination tests.
>
> Designed a quality-gated SAR cascade with vLLM + 4-bit AWQ, cutting model memory 63.5%, p95 latency 36.3%, and boosting throughput 60.8% vs. BF16 at concurrency 32; citation-failed drafts auto-escalate to BF16, benchmarked on 1,000 cases.

**Bullet 1 is essentially already true.** LangGraph, XGBoost/SHAP, ChromaDB, AKS/Terraform/HPA, and the citation/hallucination suites exist and are real (verified against source, not filenames).

**Bullet 2 is half true.** The measured RunPod RTX 4090 run (`vllm-bench-f810b57a7b8ae05a`, 1,000 IBM-derived synthetic cases, concurrency 1/8/32) already substantiates every percentage. What does **not** exist is the thing the bullet is named after: there is **no quality gate on the production path and no escalation of any kind**. `evaluate_draft_checks` — the deterministic gate — is reachable only from the multi-agent graph, the offline benchmark, and tests. The default single-writer path calls `parse_and_ground`, which **silently deletes fabricated citation IDs** and returns a draft that looks clean.

That silent drop is also why the original run failed acceptance on `reference_validity` (0.839 vs required 1.0).

**The outcome this release must produce:** a real deterministic gate on the production path, a real AWQ→BF16→external cascade with explicit failure, and a re-benchmark of that cascade so the bullet describes shipped behaviour.

### Pre-computed evidence (zero GPU spend, from the persisted run)

The prior run persisted full per-case raw output, latency, TTFT, and token usage for **1,000 cases × 2 arms × 3 levels** in `.local/vllm-bench/vllm-bench-f810b57a7b8ae05a/run.json`. Replaying the proposed gate over it reproduces the published −36.3% p95 exactly, which validates the method, and projects the cascade:

| Metric @ c32 | BF16 | AWQ raw | Gated cascade (projected) |
|---|---:|---:|---:|
| Deterministic gate pass | 94.1% | 73.9% | **96.1%** |
| Escalation rate | — | — | **26.1%** |
| Both tiers fail | — | — | 3.9% |
| p50 | 15,036 ms | 9,329 ms | 9,899 ms |
| **p95** | 19,426 ms | 12,373 ms | **28,930 ms (+48.9%)** |
| GPU time | baseline | — | **−10.5%** |

Three decisive facts:

1. **Every gate failure in both arms is citation fabrication.** `unsupportedClaimFlags` is 0 everywhere — because the single-writer prompt never asks for `claims`, so `SarDraftContent.claims` is always empty and claim-grounding is vacuous.
2. **The naive cascade's p95 is worse than BF16** (+48.9%): an escalated case pays both models. This is the central engineering problem of the release and why schema-constrained decoding is in scope, not optional.
3. **Citation membership is not enough.** A claim asserting an altered amount while carrying a *valid* evidence reference passes every check that exists today. The current adversarial fixtures only fail detection because their `evidence_refs` are **empty** — the gate has never been tested against a plausible-looking lie.

### Decisions taken

- **Constrained decoding + honest reporting.** Citation IDs become a closed JSON-Schema enum so fabrication is structurally impossible; the gate stays as defence-in-depth; both constrained and unconstrained configurations are reported.
- **Full live re-benchmark**, funded by a reserve draw in `config/experiments/budget.yaml`, scoped by a free replay pilot.
- **External Tier 3 decided by a fixed pilot, not assertion.** Candidates: `openrouter/anthropic/claude-sonnet-4.6` (prior) and `openrouter/openai/gpt-5-mini`. Run both over the *same* fixed 100-case set drawn from real both-tier-failure cases; the cheapest candidate clearing every threshold wins; ties go to gpt-5-mini on cost. Model is frozen before the full run and never changed after seeing 1,000-case results.
  - *Prior and reasoning:* only `claude-sonnet-4.6` and `claude-opus-4.6` are both `structured_output: true` and `intelligence: high`. gpt-5-mini, grok-4.3, and gemini-2.5-flash are all `intelligence: medium` — the same band as the Qwen2.5-7B they'd escalate from, so escalating to them is a lateral move. Counter-argument (worth testing): gpt-5-mini costs ~$0.05/1k vs Sonnet's ~$0.42/1k and gives provider-family diversity after two Qwen stages. The pilot settles it.
  - `openrouter/qwen/qwen3.5-plus-02-15` is the open-weight contender (`intelligence: high`, $0.26/$1.56) but the catalog does not declare `structured_output`, so `require_generation_capabilities` would reject a schema request. Qualifying it = verifying and recording that capability. Documented alternative, not shipped blind.
  - **All hosted calls go through OpenRouter.** Self-hosted AWQ/BF16 stay direct vLLM — they are the subject of the benchmark, and you cannot measure GPU weight memory of a model you do not host.
  - **Every OpenRouter request must enforce zero-data-retention** (`provider.zdr`, data-collection denial) and the selected route must be **revalidated at readiness**, not trusted from static YAML. Route eligibility is dynamic.

### Non-goals

- Rewriting `LiveSarDrafter`, `MultiAgentSarDrafter`, the LangGraph graphs, `LlmClient`, or the benchmark harness. All are sound and stay.
- Deleting `agents/checks.py`. It is used by the multi-agent graph, the benchmark, and 3 test files. **Compose it, do not replace it.**
- Relocating `agents/checks.py` into `fraudlens-ml` — architecturally tempting, no functional payoff. Deferred.
- Any GPU workload on AKS. AKS is and remains CPU-only app serving.
- An always-on RunPod deployment. The cascade ships production-capable; the deployed profile stays OpenRouter-only.

---

## Phase 1 — Repository and gap analysis

### Objective
Freeze a verified, source-backed statement of what each resume clause supports, so later phases cannot drift back into assumption.

### Existing code to inspect or reuse
- `README.md` — published claims; `plans/README.md:32-38` — the 5 open "Release 0.4.0 scope" items that are the de-facto 0.5.0 backlog (restated as items 1-6 of `docs/handoff/0.3.0-additions.md:39`)
- `backend/src/fraudlens_backend/sar/` — `drafter_live.py`, `drafter_mock.py`, `drafter_multi_agent.py`, `drafter_fallback.py`, `drafter_replay.py`, `factory.py`, `schema.py`, `egress.py`, `prompt.py`, `budget.py`, `cache.py`
- `backend/src/fraudlens_backend/agents/checks.py` — `DeterministicReviewChecks`, `evaluate_draft_checks`
- `packages/fraudlens-ml/src/fraudlens_ml/evaluation/citations.py` — `CitationMetrics`, `CoverageMetrics`
- `packages/fraudlens-ml/src/fraudlens_ml/sar/protocol.py` — `SarDrafter`, `SarInput`, `SarDraftContent`, `SarClaim`, `SarDraftResult`
- `packages/fraudlens-llm/src/fraudlens_llm/client.py` — `LlmClient`, `_generate_with_fallbacks`, `eligible_fallbacks`
- `scripts/lib/vllm_bench/` — 16 modules, 4,205 lines
- `docs/reference/benchmarks/vllm-awq-sar-benchmark.{json,md}`, `docs/reference/claims.md`, `docs/reference/experiments/ledger.md`

### Traceability matrix

| Resume claim | Existing evidence | Gap | Required work |
|---|---|---|---|
| LangGraph | **Complete.** Two real compiled `StateGraph`s: `fraudlens_ml/pipeline/graph.py:225` (6 nodes, conditional short-circuit), `agents/graph.py:357` (4 agents, parallel fan-in, structural revision cap) | none | regression only |
| XGBoost | **Complete.** Real `xgb.DMatrix` inference + Platt calibration; fitted artifacts committed incl. a 23.6 MB full-data model | none | none |
| SHAP | **Complete.** Real `TreeExplainer` on raw margin, additivity asserted to 1e-3 (`test_scoring_explainer.py:39`); drivers reach the prompt via `sar/prompt.py:141` | none | none |
| ChromaDB | **Complete.** Real `chromadb` with lexical degradation and fenced citations | none | none |
| citation-grounded drafts | **Partial.** `ground_citations` is exact-ID set membership | **Silently drops** fabricated IDs; claims can reference dropped IDs; prompt never requests claims | Phase 2 — gate before grounding, reject don't remove |
| AKS + Terraform + HPA | **Complete, one weak artifact.** Hardened cluster, real `autoscaling/v2` HPA (1→5, CPU 60%), applied + measured + torn down (1→3→5→1, 101 s) | Published load shows **1,147 / 783,498** succeeded (~99.85% rate-limited); validator only requires `succeeded > 0`; undisclosed | Phase 5 — disclose or re-run within the rate limit; keep "ephemeral demonstration" wording |
| citation-quality + hallucination tests | **Partial.** Egress gate is genuinely strong (sockets denied, byte-level assertions) | The 32-scenario citation gate is **tautological** (MockSarDrafter's `cited_regulations` ≡ offered set). Adversarial fixtures pass only because refs are **empty** | Phase 3 — replay fixture + valid-ref/wrong-value fixtures |
| vLLM + 4-bit AWQ | **Complete.** Real RunPod run, 13,006 telemetry samples, digest-pinned image, revision-pinned arms | none | reuse pins |
| memory −63.5% | **Complete.** `weightMemoryReduction: 0.6348` | Weight-only; cascade footprint differs | Phase 4 — re-verify; report weight *and* peak device memory *and* two-endpoint aggregate separately |
| p95 −36.3% | **Reproducible but unrecorded.** `36.3` appears **nowhere**; `mechanical_headline()` templates only memory + throughput. Derived: (19425.52−12372.65)/19425.52 = 36.307% | Not a published claim | Phase 4 — add latency to the mechanical headline |
| throughput +60.8% | **Complete.** `(3.351/2.083−1)×100` | Raw req/s only; AWQ *useful* throughput was **worse** (1.340 vs 1.552) | Phase 4 — report raw req/s and quality-passing cases/s separately |
| concurrency 32, 1,000 cases | **Complete.** Config-driven, not hardcoded | none | none |
| **"quality-gated SAR cascade"** | **Not defensible.** No `QualityGate`/`GateResult` type exists; `escalat*` matches only alert lifecycle | **The entire feature** | Phase 2 |
| **"citation-failed drafts auto-escalate to BF16"** | **Not defensible.** `SarLlmConfig.fallbacks` is transport/governance fallback inside `LlmClient` | **The entire feature** | Phase 2 |

### Dead / duplicate code relevant to this release

| Item | Location | Disposition |
|---|---|---|
| `SarInput.rag_context` computed, **zero production readers** | `pipeline_ports.py:125`, `steps.py:166`, `egress.py:28` | Phase 5 — remove field + producers; fix stale docstring `sar/prompt.py:9` |
| `SarQualityStatus` — hardcoded `EVALUATED`, no evaluator | `db/models/enums.py:128`, `db/repositories/sar.py:77,114` | Phase 2 — becomes tri-state `not_run`/`passed`/`failed` |
| `fraudlens_core.types.TransactionSummary` — self-described placeholder, used only in `test_core.py` | `fraudlens_core/types.py` | Phase 5 — delete |
| Empty `config/quality/` directory | — | Phase 5 — delete |
| Two SAR-quality metric implementations with different empty-set conventions, plus a third divergent error-path/aggregation policy | `fraudlens_ml/evaluation/citations.py:99` (empty produced set → `float(not expected)`), `sar_eval/report.py:93` (empty cited set → `0.0`); `vllm_bench/quality.py` already delegates to `fraudlens_ml.evaluation` but zeroes validity/recall on its own error paths (`:89,:104,:165`) | Phase 5 — converge the gate on `fraudlens_ml.evaluation` and fold `vllm_bench`'s error-path convention into it; leave `sar_eval` |
| `benchmark-vllm` SKILL.md references **non-existent** `make gpu-bench-*` and allocation `azure_gpu_benchmark` | `.claude/skills/benchmark-vllm/SKILL.md:22,30,36,42,43` | Phase 4 — **blocking**, runbook is literally unrunnable |
| `.claude/settings.json` ask-gate covers dead `gpu-bench-*`; live billable `runpod-gpu-{up,down,start,stop,sync}` have **no entry** | `.claude/settings.json:50-52,108-109` | Phase 4 — **blocking** before any paid run |
| `AGENTS.md:265` claims a `gpu-bench` Terraform root that does not exist | `AGENTS.md` | Phase 5 |
| "$25 GPU allocation" prose; actual `$10.00` | `docs/runbooks/vllm-benchmark.md:24`, `runpod_gpu/planning.py:8` | Phase 5 |
| Unused frontend exports flagged by `make deadcode` | frontend | Phase 5 — remove exports only where references prove them unused |

### Architectural decisions
- **AD-1.1** Preserve arm-level claims. −63.5% / −36.3% / +60.8% describe raw AWQ vs BF16 at c32 and stay valid. Cascade metrics are reported **separately**, never as a restatement.
- **AD-1.2** No new abstraction where `DeterministicReviewChecks`, `CitationMetrics`, `CoverageMetrics`, `SarDraftResult`, or `SarDrafter` already fits.
- **AD-1.3** The v1 report and JSON stay published and immutable as historical evidence, even after their runtime code is retired.

### Tests and validation
Record current baselines: `make deadcode`, `make quality-gates`, `make vllm-bench-validate`, `make k8s-validate`. Verify published report hashes unchanged.

### Verification record — executed 2026-09-16 at `cfbabbf` (branch `release/0.5.0`)

**Baselines.** All four read-only gates green at the pre-implementation commit:

| Command | Result |
|---|---|
| `make quality-gates` | 15 passed |
| `make vllm-bench-validate` | `vllm-bench validation OK (provider-free smoke protocol)` |
| `make k8s-validate` | 43 resources / 41 valid / 0 invalid / 2 skipped; checkov 714 passed, 0 failed; 13 manifest tests passed |
| `make deadcode` | vulture clean, ruff F401/F811/F841 clean; knip 2 unused exports + 8 unused exported types (frontend only, advisory) |

The 10 knip findings are the Phase 5 frontend cleanup candidates: `upsertAgentRun`
(`frontend/src/lib/investigationReducer.ts:133`), `sarEvalHeadline` (`frontend/src/pages/SarEvalStudy.tsx:41`),
and the types `AgencyStyle`, `StudyMotifNode`, `StudyMotifEdge`, `LayoutNode`, `LayoutInput`,
`GraphLayout`, `PortfolioDemoPersonas`, `SarEvalStudyProps`.

**Published evidence unchanged.** `git status --porcelain docs/reference/benchmarks/` is empty, and
the published artifact is byte-identical to the persisted run
(`sha256 799733ff4043606acaa7e3e07607e7e2aed654f26fdd9ee0ee252b43d5551b25` for both
`docs/reference/benchmarks/vllm-awq-sar-benchmark.json` and
`.local/vllm-bench/vllm-bench-f810b57a7b8ae05a/report.json`; `c8ca8cee401e…` for both `.md` files).
Lineage holds: the report's `casesSha256 b3367d9e742a…` equals the case artifact's `artifactSha256`,
and its `promptSha256 9b55faf4deb7…` still equals `sha256(config/llm/prompts/sar/v1.md)` — so the
prompt is unmodified since the run and is a valid freeze point for the Phase 2 prompt version bump.
AD-1.3 holds. Pins for Phase 4 reuse: image digest
`sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6` (`vllm/vllm-openai:v0.10.2`),
BF16 revision `a09a35458c702b33eeacc393d103063234e8bc28`, AWQ revision
`b25037543e9394b818fdfca67ab2a00ecc7dd641`.

**Context evidence table reproduced.** Replaying the *existing* `evaluate_draft_checks` over all
6,000 persisted measurements (read-only, no repo changes) reproduces every projected figure:
gate pass BF16 94.1% / AWQ 73.9% at c32, escalation 26.1%, cascade final pass 96.1%, both-tiers-fail
3.9%, cascade p95 28,929.9 ms (+48.93% vs BF16 19,425.5 ms), GPU time −10.52%. Published percentages
recompute exactly from the JSON: p95 −36.3073%, req/s +60.8206%, `weightMemoryReduction` 0.634804.
Only the cascade p50 differed from the drafted table (9,899 ms measured, nearest-rank, vs 9,903 ms
drafted) — corrected above.

**The three decisive facts are confirmed at source**, not inferred:

1. All 912 gate failures across the 6,000 measurements are citation fabrication only —
   `unsupported_claim_indexes` is empty in every one. The 6,000 drafts produced **0** `SarClaim`
   objects, because `config/llm/prompts/sar/v1.md` requests only `subject`, `narrative`, `sections`,
   `citedRegulations`, and `recommendedAction`; only the multi-agent prompt
   (`config/llm/prompts/agents/sar_writer/v1.md:17`) asks for `claims`.
2. Cascade p95 is worse than BF16 by 48.93%, reproduced above.
3. `evaluate_draft_checks` flags a claim only when `evidence_refs` is empty or unresolvable
   (`agents/checks.py:79-87`); the adversarial fixtures at `tests/fixtures/adversarial_drafts.py:47`
   plant exactly that shape, so a false statement carrying `SUPPORTED_REF` would pass today.

**Corrections applied to this plan** (drafted figure → verified figure): telemetry samples
12,006 → **13,006** (7697+1138+439 BF16, 2935+525+272 AWQ); `agents/checks.py` consumers
4 test files → **3** (`tests/unit/test_agent_graph_traversal.py`,
`tests/quality/test_hallucination_detection.py`, `tests/security/test_agent_adversarial_grounding.py`);
the 5 open scope items live in `plans/README.md:32-38`, not `README.md`; cascade p50
9,903 ms → **9,899 ms**; and the metric-stack row, because `vllm_bench/quality.py:25` already imports
`citation_precision_recall`/`required_fact_coverage` from `fraudlens_ml.evaluation` — there are two
implementations, not three, plus a third divergent error-path convention.

### Acceptance criteria
- Matrix committed to `plans/`.
- Every resume clause classified complete / partial / not yet defensible.
- No new AKS or Azure-GPU work proposed.
- Cleanup candidates proven unused by reference search before any deletion.

**Status: met, except that the commit itself awaits the owner.** The matrix and this record are
written to `plans/` and staged; Golden Rule 1 forbids committing without explicit permission, so the
first criterion is satisfied as far as an agent may take it. Every matrix row is cited to a file and
line above or in the matrix itself, and each is classified complete / partial / not defensible. The
plan proposes no new AKS or Azure-GPU work: the only AKS items are the existing artifact's
disclosure and README wording (Phase 5, "Disclose the AKS load caveat") and a read-only
`k8s-validate`, under an explicit "do not modify AKS Terraform unless a compatibility test proves an
actual defect" constraint, with "any GPU workload on AKS" a stated non-goal; the re-benchmark runs
on RunPod, and no Azure allocation grows. Every deletion candidate was proven unused by reference
search before Phase 5 may act on it:

| Candidate | Proof of non-use |
|---|---|
| `SarInput.rag_context` | Produced at `pipeline_ports.py:125`, `steps.py:166`, `cases_fixture.py:168`, and empty at `sar_regeneration.py:187`; **zero readers** — `sar/prompt.py:_render_user_content` renders `_render_regulations`, never `rag_context`, and `sar/egress.py:28` documents that it is ignored. The `sar/prompt.py:9` docstring claiming otherwise is stale. |
| `SarQualityStatus` | `db/repositories/sar.py:77` hardcodes `EVALUATED` on every machine draft; `:114` sets `UNEVALUATED` only on human edits. No evaluator exists, so the field never reflects quality. |
| `fraudlens_core.types.TransactionSummary` | Defined `types.py:34`, re-exported `fraudlens_core/__init__.py:47`; the only consumer in the repo is `tests/unit/test_core.py`. Self-described placeholder in its own module docstring. |
| `config/quality/` | Empty on disk and untracked (`git ls-files config/quality` returns nothing); distinct from the live `config/quality.yaml`. |
| `make gpu-bench-*` targets | No `gpu-bench` target exists in the `Makefile`; the live equivalents are `runpod-gpu-*` (`Makefile:502-538`). Referenced by `.claude/skills/benchmark-vllm/SKILL.md:22,30,36,42,43` and gated by `.claude/settings.json:50-52,108-109`, while the billable `runpod-gpu-{up,down,start,stop,sync}` have **no ask-gate entry** — blocking for Phase 4. |
| `azure_gpu_benchmark` allocation | `config/experiments/budget.yaml` defines `azure_cpu_batch`, `gpu_benchmark`, `e2e_application_pass`, `supporting_resources`, `reserve`. The name in SKILL.md:36 does not exist. |
| `gpu-bench` Terraform root | `infra/terraform/environments/` holds `aks-demo`, `cost-guardrails`, `data-batch`, `dev`, `prod`. `AGENTS.md:265` names a root that is absent. |
| "$25 GPU allocation" prose | `gpu_benchmark` is `"10.00"`; `"25.00"` is the untouched `reserve`. Stale at `docs/runbooks/vllm-benchmark.md:24` and `scripts/lib/runpod_gpu/planning.py:8`. |
| Unused frontend exports | The 10 knip findings listed above. |

Two further stale artifacts observed while verifying, for Phase 5 to fold in:
`docs/reference/benchmarks/k8s-hpa-scaling.json` carries no run id in any casing (the AKS file uses
`run_id: aks-demo-20260915-01`), and `docs/handoff/0.3.0-additions.md:45` still lists the completed
AKS demonstration as deferred, unlike `plans/README.md:39`.

Spend context for the Phase 4 reserve draw, from `docs/reference/experiments/ledger.md:53-54`: the
original benchmark session cost **$5.92** actual against the `gpu_benchmark` **$10.00** allocation,
and the AKS session **$0.79** against `supporting_resources`; both are teardown-verified.

### Dependencies
None.

---

## Phase 2 — Production SARQualityGate and cascade

### Objective
Make deterministic quality validation mandatory between every generated candidate and persistence, with finite, auditable escalation and explicit failure.

```
SAR request
  └─ preflight: evidence non-empty + egress-eligible  ── fail ──▶ explicit failure, zero spend
  └─ Tier 1 (AWQ, self-hosted vLLM @ runpod-awq)
       └─ parse → SARQualityGate (deterministic, no LLM)
            ├─ PASS → ground → render → emit tokens → return
            └─ FAIL → Tier 2 (BF16, self-hosted vLLM @ runpod-bf16)
                 └─ parse → SARQualityGate
                      ├─ PASS → return (escalation_tier=1)
                      └─ FAIL → Tier 3 (OpenRouter, ZDR-enforced), egress-permitting
                           └─ parse → SARQualityGate
                                ├─ PASS → return (escalation_tier=2)
                                └─ FAIL → explicit typed failure, status=failed
```

### Existing code to reuse

| Reuse | Why |
|---|---|
| `evaluate_draft_checks` / `DeterministicReviewChecks` (`agents/checks.py:59,25`) | **Already the citation/evidence half of the gate.** Compose; do not reimplement or delete |
| `citation_precision_recall`, `required_fact_coverage` (`evaluation/citations.py:82,145`) | Metrics for the structured result |
| `LiveAgentFallbackDrafter` (`sar/drafter_fallback.py`) | **The decorator pattern to copy.** Wraps drafters behind the protocol, refuses a mock fallback |
| `SarDrafter` protocol (`protocol.py:305`) | The seam; the gated drafter is another implementation |
| `SarClaim.evidence_refs` / `.citation_ids` (`protocol.py:112`) | Claim→source traceability already modelled |
| `SarModelInput` + `project_for_model` (`sar/egress.py`) | **The canonical fact source** for asserted-fact validation, already PHI-free and digest-verified |
| `_available_evidence_refs` (`agents/graph.py:466`) | Trusted evidence-ref harvester — reuse, don't write a second |
| `LlmClient.generate()` `response_schema` (`client.py:146`) + catalog `structured_output` (`catalog.py:139`) | Constrained decoding **already supported on the non-streaming path** |
| `BudgetGuard`, `SarDraftCache`, `stream_result`, `SarStreamEvent` | Budget, cache, SSE vocabulary |

### Changes required

**2.1 — Evidence catalog (deterministic, from existing projection)**

Build from the already-projected `SarModelInput` — no new data source, no PHI, no DB ids:
- Stable synthetic ref ids: transaction amount/date/country/channel, risk band, rule ordinal, SHAP feature, aggregate, regulation id
- Canonical **typed** values: `Decimal` for money, UTC `datetime`, controlled enums for country/channel/rule type
- This catalog is what claims must reference *and* match

**2.2 — Prompt v2** (`config/llm/prompts/sar/v2.md`; prompts are versioned + hashed — do **not** mutate v1)

Require: non-empty structured `claims`; exact evidence refs drawn from the catalog; per-claim citation ids; **machine-readable asserted facts** for every deterministic value; explicit `unknown` rather than inference; and coverage of the FinCEN who/what/when/where/why/how narrative elements using masked aliases (README backlog item 5).

**2.3 — `SarQualityGate`** (new: `backend/src/fraudlens_backend/sar/quality_gate.py`, ~220 lines)

Pure. No IO, no provider calls, no LLM.

```python
class SarGateReason(StrEnum):
    SCHEMA_INVALID = "schema_invalid"
    NO_CITATIONS = "no_citations"
    CITATION_FABRICATED = "citation_fabricated"
    CITATION_DUPLICATED = "citation_duplicated"
    CLAIM_MISSING_EVIDENCE = "claim_missing_evidence"
    EVIDENCE_REF_UNRESOLVED = "evidence_ref_unresolved"
    EVIDENCE_EMPTY = "evidence_empty"
    ASSERTED_FACT_MISMATCH = "asserted_fact_mismatch"  # ← the hallucination check
    UNMAPPED_NARRATIVE_FACT = "unmapped_narrative_fact"
    FINCEN_ELEMENT_MISSING = "fincen_element_missing"
    OUTPUT_TRUNCATED = "output_truncated"


class SarQualityGateResult(BaseModel):  # frozen, camelCase, extra="forbid"
    passed: bool
    policy_version: str
    policy_hash: str
    reasons: tuple[SarGateReason, ...]
    checks: DeterministicReviewChecks  # reused verbatim
    citation_metrics: CitationMetrics  # reused verbatim
    fabricated_citation_ids: tuple[str, ...]
    duplicate_citation_ids: tuple[str, ...]
    unsupported_claim_indexes: tuple[int, ...]
    mismatched_facts: tuple[SarFactMismatch, ...]
    unmapped_narrative_spans: tuple[int, ...]
    missing_fincen_elements: tuple[str, ...]
    fallback_required: bool
```

Rules, **all runtime-computable without ground-truth labels**:
- Schema validity — delegate to `parse_content`
- Citation existence / validity / duplication — `checks.fabricated_citation_ids` empty, deduped length equal
- Claim grounding — `checks.every_claim_has_evidence`, `checks.evidence_refs_are_available`
- **Asserted-fact equality** — each `SarClaimFact` compared to its catalog value by canonical type (`Decimal` equality, UTC instant equality, enum identity); normalisation is explicit and total
- Narrative mapping — every objective value appearing in subject/narrative/sections must resolve to an asserted fact
- FinCEN elements present or explicitly marked unavailable
- Truncation — `finish_reason == "length"`

**Explicitly excluded as runtime rules:** `citation_recall` and `required_fact_coverage` need benchmark ground truth (`expectedCitationIds`, `requiredFacts`) that does not exist at runtime. They remain benchmark-only aggregate metrics. Conflating them would make the gate un-runnable in production — this is the most important scoping decision in the phase.

**2.4 — Surface the pre-grounding verdict**

`parse_and_ground` erases fabricated ids, so a downstream decorator can never see them.
- `sar/schema.py` — add `parse_only(raw_text)` (alias of `parse_content`, no behaviour change). `parse_and_ground` stays for multi-agent.
- `drafter_live.py` (238 → ~280) — parse, gate the **ungrounded** content, attach result, *then* ground and render.
- `fraudlens_ml/sar/protocol.py` — add `SarEvidenceFact`, `SarClaimFact`, `SarFactMismatch`, `SarQualityGateResult`, `SarGenerationAttempt`; extend `SarClaim` with `asserted_facts`; extend `SarDraftResult` with `quality`, `attempts`, `escalation_tier`, `escalated_from`. *Layering: these value types live in `fraudlens-ml`; the evaluator lives in the backend, so `fraudlens-ml` never imports backend or llm.*

**2.5 — `QualityGatedSarDrafter`** (new: `backend/src/fraudlens_backend/sar/drafter_gated.py`, ~200 lines)

Modelled on `LiveAgentFallbackDrafter`.

- **Buffer, then emit.** Model output is buffered until a tier passes the gate. `sar.token` events are emitted **only** for the accepted result. Tokens from a rejected AWQ or BF16 attempt must never reach a client.
- Emits PHI-free lifecycle events: `sar.stage.started`, `sar.stage.rejected` (reason codes only), `sar.escalated`, `sar.cascade.failed` — new `SarEventType` members, giving the frontend the trace (README backlog item 3).
- **Retry boundary:** exactly one generation per tier. No same-tier retry — a deterministic gate failure is not transient, and retrying at temperature 0 is pure waste. The `LlmClient`'s bounded transport retries remain *inside* a tier; the cascade passes `fallbacks=()` so the client never model-hops behind its back.
- **Recursion guard:** tiers are a finite ordered tuple consumed by index; no tier may reference another; `len(tiers) <= max_tiers` enforced at construction. Max generations = number of unique configured stages.
- **Escalate on:** exhausted retryable provider error, timeout, rate limit, empty output, malformed schema, gate failure.
  **Stop immediately on:** egress refusal, tenant/policy denial, invalid configuration, insufficient source evidence, exhausted budget.
- **Budget checked before *each* tier**, not once per request.
- **Preflight** rejects empty or policy-ineligible evidence before any model call — zero spend on a case that cannot succeed.
- **External output is not trusted more than local output** — the same gate runs after Tier 3.

**2.6 — Constrained decoding**

`generate_stream` hardcodes `response_schema=None` (`client.py:184`) while `generate()` threads it (`client.py:146`).
- `StreamGenerationRequest` — add `response_schema`
- `generate_stream` — pass it into `_prepare_generation`
- Guard on catalog `structured_output` via `require_generation_capabilities`; a schema request to a model lacking it must **raise**, never silently downgrade
- `sar/schema.py` — `sar_response_schema(available)` building the `SarDraftContent` schema with `citedRegulations.items.enum` and `claims[].citationIds.items.enum` set to exactly the offered ids

This makes fabrication structurally impossible and returns cascade p95 to ≈ AWQ p95.

**2.7 — Cache must not bypass the gate**

`sar_cache_key` currently binds model, prompt hash, agency, model input, generation settings. Add **profile id, quality-policy hash, and prompt version**. A policy or prompt change must invalidate cached drafts, otherwise a pre-gate draft replays straight past the gate.

**2.8 — Multi-agent path gated too**

`MultiAgentSarDrafter`'s final result must pass the **same** gate before persistence. A draft is a draft. `LiveAgentFallbackDrafter` keeps its distinct role (agent-workflow → single-writer fallback) and is not merged into the cascade.

**2.9 — Audit persistence**

New tenant-scoped `sar_generation_attempts` (agency_id, run_id, draft_id, ordinal): stage name, model ref, connection route, served model, outcome, reason codes, gate result, latency, retry count, token usage, cost, prompt hash, policy hash. `SarDraftResult` aggregates usage/cost across attempts and `fallback_count` becomes exact. `SarQualityStatus` → `not_run` | `passed` | `failed`, with historical rows backfilled to `not_run`.

**2.10 — Wire it**

`sar/factory.py` `build_sar_drafter` gains a `tiers` branch. A single-model config keeps today's behaviour exactly — no flag, no parallel path.

### Architectural decisions
- **AD-2.1 Decorator, not rewrite.** The cascade is a `SarDrafter` wrapping `SarDrafter`s. Keeps `drafter_live.py` under the 500-line cap (262 lines headroom; `agents/graph.py` has **6** and must not be touched) and makes the cascade reusable unchanged.
- **AD-2.2 Gate evaluates pre-grounding output.** Post-grounding the evidence of fabrication is already destroyed.
- **AD-2.3 Quality fallback ≠ transport fallback.** `SarLlmConfig.fallbacks` (governance-gated, inside `LlmClient`, fires on retryable transport errors) and `tiers` (quality-triggered, outside the client) are different concerns at different layers. Both kept; distinction recorded in the ADR. `sar-vllm.yml` keeps `fallbacks: []` so a self-hosted transport failure never silently leaves the GPU.
- **AD-2.4 Gate is deterministic, never an LLM.** Every rule is set membership, count, typed equality, or enum comparison.
- **AD-2.5 Named connections.** `providers.yml` currently has **one** `vllm` entry with one `base_url_env`, so two tiers would resolve to the same endpoint — the cascade cannot work without this. Split provider **governance** (posture, data classes, retries) from named **connections** (`runpod-awq`, `runpod-bf16`, `openrouter-zdr`), each with its own runtime-injected URL/key env names. Tiers select a connection by name; no URL or secret in config.

### Configuration changes

`config/llm/sar-vllm.yml` — ordered cascade profiles replacing the flat single model:
```yaml
profiles:
  deployed-openrouter:                 # always-on shape, unchanged behaviour
    - {name: external, model: openrouter/openai/gpt-5-mini, connection: openrouter-zdr}
  bf16-baseline:
    - {name: bf16, model: vllm/Qwen/Qwen2.5-7B-Instruct, connection: runpod-bf16}
  awq-raw:
    - {name: awq,  model: vllm/Qwen/Qwen2.5-7B-Instruct-AWQ, connection: runpod-awq}
  awq-bf16:
    - {name: awq,  model: vllm/Qwen/Qwen2.5-7B-Instruct-AWQ, connection: runpod-awq,  constrained_decoding: true}
    - {name: bf16, model: vllm/Qwen/Qwen2.5-7B-Instruct,     connection: runpod-bf16, constrained_decoding: true}
  awq-bf16-external:
    - {name: awq,  model: vllm/Qwen/Qwen2.5-7B-Instruct-AWQ, connection: runpod-awq,  constrained_decoding: true}
    - {name: bf16, model: vllm/Qwen/Qwen2.5-7B-Instruct,     connection: runpod-bf16, constrained_decoding: true}
    - {name: external, model: <pilot-selected>, connection: openrouter-zdr,
       constrained_decoding: true, requires_egress_class: synthetic}
max_output_tokens: 3000
```

`config/llm/providers.yml` — provider governance + named connections; `openrouter-zdr` carries mandatory ZDR / data-collection-denial request options and an allowed-upstream list.

`config/quality.yaml` — extend the existing `sar_quality` block (do **not** create a second source):
```yaml
sar_quality:
  # existing benchmark thresholds unchanged
  runtime_gate:
    policy_version: sar-gate-v1
    require_citation: true
    allow_duplicate_citations: false
    require_claim_evidence: true
    require_asserted_fact_match: true
    require_fincen_elements: true
    fail_on_truncation: true
    max_tiers: 3
```

`config/llm/catalog.yml` — no new entries; both pilot candidates are present, `callable: true`, `structured_output: true`.
`config/llm/prompts/sar/v2.md` — new versioned prompt.

### Code and dead-code cleanup
- Silent citation removal stops being the guardrail; `ground_citations` stays (multi-agent uses it) but its docstring must stop claiming it is.
- `SarQualityStatus` hardcoding removed at `db/repositories/sar.py:77,114`.
- `SarLlmConfig.fallbacks` retained **only** as transport fallback, documented as such.
- No compatibility shim, no old/new flag.

### Acceptance criteria
- **No result with `status=draft` can exist without `quality.passed=true`.** Enforced at the repository boundary, not by convention.
- A fabricated citation id produces gate failure + escalation, never a silently shortened list.
- A claim asserting a wrong amount/date/country **with a valid evidence ref** fails with `ASSERTED_FACT_MISMATCH`.
- Rejected-tier tokens never reach a client.
- All tiers failing → `status=failed`, `error_code="sar_quality_gate_failed"`, populated `quality`.
- Attempts, costs, and provenance reconstructable from tenant-scoped PHI-free rows.
- At most one generation per configured stage (excluding a connection's bounded native retries).
- A schema request to a model without `structured_output: true` raises.
- The deployed OpenRouter-only profile needs no RunPod endpoint and behaves as today.
- `make ci` green; `make file-length-check` green.

### Dependencies
Phase 1.

---

## Phase 3 — Test and validation strategy

### Objective
Prove correctness, failure containment, determinism, and non-regression before any paid benchmarking.

### Existing code to reuse
`tests/conftest.py` (`sandbox`, `make_sar_input`, `make_settings`, `client_factory`); `tests/fixtures/openai_compatible_fake.py` (`CapturedOpenAiEndpoint` — httpx `MockTransport`, captures request **bytes**, injects 500s); `tests/fixtures/pipeline_fakes.py` (`FakeSarDrafter`); `tests/fixtures/adversarial_drafts.py`; the socket-denial pattern in `tests/quality/test_model_egress.py`; `scripts/lib/quality/config.py`.

### Unit — `tests/unit/test_sar_quality_gate.py` (new)
One test per `SarGateReason`; clean draft passes; empty citations under `require_citation: false` passes; duplicates fail; `finish_reason="length"` fails; **asserted-fact mismatch on amount / date / country / risk / rule type with a *valid* ref**; unmapped narrative fact; missing FinCEN element; policy round-trips from `config/quality.yaml`; **determinism** — same input 100× yields byte-identical `model_dump_json()`; normalisation total (no silent coercion).

### Unit — `tests/unit/test_sar_drafter_gated.py` (new)
Scripted `FakeSarDrafter` tiers:
1. AWQ passes → BF16 never invoked, `escalation_tier=0`
2. AWQ fails → BF16 passes → `escalation_tier=1`, one `sar.escalated`
3. AWQ + BF16 fail → external passes → `escalation_tier=2`
4. All fail → explicit exhaustion, quality attached
5. Malformed output (unfenced garbage, truncated JSON, `extra="forbid"` violation) → `schema_invalid` → escalates
6. Timeout / rate limit / empty output → escalates, transport code preserved and distinct from a gate failure
7. Non-escalatable: egress refusal, budget exhaustion, invalid config, insufficient evidence → **stop**, no further tier
8. External tier invoked **only** when enabled and egress-eligible; otherwise skipped with `egress_tier_not_allowed` and zero request bytes
9. **No recursion** — over-long tier list raises at construction; each tier invoked exactly once
10. Exact aggregate token/cost/`fallback_count` accounting across attempts
11. Preflight rejects empty evidence with **zero** model calls

### Integration — `tests/integration/test_sar_cascade_live.py` (new)
Real `LiveSarDrafter`s over `CapturedOpenAiEndpoint` on three distinct fake connections. Asserts: tier-2 projected payload is byte-identical to tier-1 (escalation leaks nothing extra); the emitted JSON Schema carries the closed citation enum; the OpenRouter request body carries ZDR + data-collection denial; **no rejected-tier text appears in any SSE frame**; no forbidden sentinel in any request body or `caplog.text` across all three tiers.

### Integration — cache, tenancy, multi-agent
Cache entry cannot be replayed after a policy/prompt/profile change and cannot bypass the gate; attempts persist under the correct `agency_id` in order; cross-tenant read/write fails; multi-agent results pass the same gate and fall back to the single-writer cascade when rejected; SSE reconnect reproduces stage decisions without rejected content.

### Model-serving — `tests/integration/test_sar_drafter_vllm_route.py` (extend)
Cascade profile resolves **two distinct connections**; `constrained_decoding: true` produces a `json_schema` response format; a vLLM 503 on tier 1 escalates; a stopped BF16 endpoint produces a bounded failure and **never loops back to AWQ**; readiness validates every active stage and revalidates OpenRouter route eligibility.

### Quality regression
- **Rewrite `tests/quality/test_citation_quality.py`.** The current test drafts via `MockSarDrafter` whose `cited_regulations` *is* the offered set — precision/recall ≡ 1.0 by construction. Replace with a committed, redacted **replay fixture** of real per-case outputs from the persisted run (both arms, including known-fabricating cases). Keep the existing "stricter config actually binds" test — it is good.
- **Replace the weak adversarial fixtures.** Current planted claims are detected only because `evidence_refs` is empty. New corpus: clean, thin-evidence, conflicting-evidence, citation-bait, malformed, explicit-unknown, and **valid-ref-with-wrong-value**.
- Retain aggregate thresholds: precision 1.0, recall ≥0.90, unsupported-claim recall ≥0.95, clean FP ≤0.05, fact coverage ≥0.95.
- New `tests/quality/test_sar_cascade_quality.py`: AWQ-arm gate pass rate within tolerance of 73.9%, BF16 of 94.1%, cascade ≥ BF16-alone.

### End-to-end — `tests/integration/test_pipeline_sar_cascade.py` (new)
Full API → durable worker → rules/scoring/SHAP/RAG → cascade → persistence → SSE. Asserts escalation events reach the channel, the draft carries `quality` + `escalation_tier`, **analyst approval is impossible for a failed or ungated result**, and scoring/investigation/regeneration/PDF/dashboard behaviour does not regress.

### Fixed 100-case external-model pilot
Drawn from **real both-tier-failure cases**. Both candidates run the identical set, prompt, policy, and sampling. Selection rule fixed in advance: cheapest candidate clearing every threshold; ties → gpt-5-mini. Model frozen before the full run.

### Architectural decisions
- **AD-3.1** Offline suites are provider-free, keyless, sockets denied. No test requires a GPU or network.
- **AD-3.2** Live model tests are approval-gated and synthetic-only.
- **AD-3.3** LLM-as-judge output may be supplementary commentary; it **never** decides runtime acceptance or release quality.
- **AD-3.4** The replay fixture is committed, redacted, and bounded (~40 cases) — non-vacuous but reviewable.

### Release-blocking for 0.5.0
`test_sar_quality_gate.py`, `test_sar_drafter_gated.py`, `test_sar_cascade_live.py`, cache/tenancy/multi-agent integration, rewritten `test_citation_quality.py`, `test_hallucination_detection.py`, `test_model_egress.py` (green across all three tiers), `test_pipeline_sar_cascade.py`, migration upgrade/downgrade, the 100-case pilot, and full `make ci`.
**Non-blocking:** cascade tolerance bands (advisory until Phase 4), `make deadcode`, challenger-model commentary.

### Acceptance criteria
- Every listed success and failure scenario has a behavioural assertion.
- No test treats successful parsing as success.
- ≥90% branch coverage on new modules; changed-file gate ≥90%.
- The rewritten citation test **fails** when the gate is disabled — proven by a deliberate temporary break.
- The pilot meets thresholds before the full run is admitted.

### Dependencies
Phase 2.

---

## Phase 4 — Gated-architecture benchmark

### Objective
Measure the real production cascade on the same 1,000 cases with the same methodology, and let measurements decide the claims.

### Existing code to reuse
The harness already has frozen protocol with 5-level hash binding, resumable per-level checkpoints, warm-up excluded and fail-closed, identical shuffled case order across arms (seeded), per-request seeds, real SSE streaming with `include_usage` (tokens never estimated), nvidia-smi/DCGM/Prometheus telemetry, per-purchase-option costing, and a mechanically-derived headline that rejects authored prose. **Extend, don't replace.**

| Reuse | Extension |
|---|---|
| `ArmConfig` / `arms` (`config.py:81`) | Generalise "arm" → **scenario** referencing a production SAR profile id |
| `run_arm` / `run_level` (`load.py:164,73`) | A cascade scenario invokes `SarCascadeDrafter`, not a raw HTTP client |
| `RequestMeasurement` (`state.py:193`) | Add `stage`, `attempt_ordinal`, `gate_passed`, `gate_reasons` |
| `LevelMetrics` (`metrics.py:33`) | Add `awq_pass_rate`, `escalation_rate`, `external_rate`, `final_pass_rate`, `stage_mix`, `gpu_hours_per_case` |
| `evaluate_case` (`quality.py:118`) | Call the **shared** `evaluate_sar_quality`; delete the locally re-derived `useful` predicate |
| `mechanical_headline` (`report_models.py:147`) | **Add p95 latency** — this is precisely why `36.3` exists nowhere |
| `server.py`, `telemetry.py`, `publish.py`, `runpod_gpu/` | Extend RunPod state from one pod to named `awq` / `bf16` roles |

### Changes required

**4.1 — Free replay pilot (zero spend, mandatory first).**
New `scripts/lib/vllm_bench/cascade.py` composes cascade metrics from the persisted per-case measurements (escalated-case latency = tier-1 + tier-2 latency for the same `case_id`). This is the admission projection ADR-028 requires **before** any paid run, and it sizes and costs the live matrix.

**4.2 — Benchmark the production path.** Scenarios invoke `SarCascadeDrafter` with prepared `SarInput` records — **not** a separate raw HTTP client and **not** a benchmark-only quality wrapper. Otherwise the benchmark measures something the product does not do.

**4.3 — Scenarios** (same dataset, order, prompt, policy, warm-up, seeds; caches disabled):
1. BF16 baseline, unconstrained — re-verifies v1
2. AWQ raw, unconstrained — re-verifies v1
3. **Gated AWQ→BF16, unconstrained** — shows the gate firing (~26% escalation)
4. **Gated AWQ→BF16, constrained** — the production recommendation
5. **Gated AWQ→BF16→external, constrained** — full cascade

Scenario 3 makes the escalation claim defensible; scenario 4 makes it fast. Both needed.

**4.4 — Concurrency scope is set by the replay pilot, not assumed.**
A naive 5 scenarios × 3 levels × 1,000 on **two** endpoints is roughly 8× the original $5.92 run — $25–45, beyond even a raised allocation. Therefore: scenarios 1–2 run all three levels on **one** endpoint (preserving v1 equal-hardware fairness); scenarios 3–5 run at **c32 only** on two endpoints unless the pilot shows the budget supports more. Final matrix and projected cost come from the pilot and are approved before provisioning.

**4.5 — Two RunPod endpoints for cascade scenarios.** AWQ and BF16 must be simultaneously reachable to measure real escalation latency; raw comparisons use one active endpoint. Independent telemetry and watchdogs per role.

**4.6 — Budget: reserve draw.** `BudgetConfig._allocation_contract_is_exact` requires the five keys to sum **exactly** to `ceiling_usd`, so a draw is a paired edit:
```yaml
gpu_benchmark: "10.00"  →  "18.00"
reserve:       "25.00"  →  "17.00"
```
Ceiling stays $75.00. Explicit owner approval (Golden Rule 7) and a ledger row before any pod is created. If the pilot projects above $18, the matrix shrinks — the allocation does not silently grow.

**4.7 — Fix governance drift before operating** *(blocking)*: SKILL.md `gpu-bench-*` → `runpod-gpu-*` and `azure_gpu_benchmark` → `gpu_benchmark`; add `.claude/settings.json` ask-gates for the live billable `runpod-gpu-*` targets and drop the dead ones; regenerate the Codex mirror via `make docs`.

**4.8 — Protocol v2.** Constrained decoding changes generation, so version rather than mutate: `protocol_version: vllm-sar-bench-v2`, plus `request.constrained_decoding` and a `cascade` section. The v1 report stays published and valid.

**4.9 — Measurement definitions** (stated in the report, not implied):
- **Case latency** = request start → terminal accepted/failed cascade result, **including** gate evaluation and all fallbacks
- **Raw throughput** = model calls/s; **useful throughput** = quality-passing cases/s (reported separately — AWQ's useful throughput was *worse* than BF16 in v1)
- Stage pass/escalation/external rates; final pass and terminal-failure rates; reason-code distribution
- Per-stage and total tokens, latency, cost, retries, serving errors by code
- **Weight memory, peak device memory, KV capacity, and two-endpoint aggregate resident footprint** — reported separately
- **GPU-hours per case** from actual RunPod active intervals, plus exact OpenRouter token cost

**4.10 — App-path budget.** `prod.yaml: llm_daily_budget_usd: 0.25` will trip `BudgetGuard` on a 1,000-case run. Raise it **explicitly and temporarily** under owner approval, record it in the ledger row, and restore it after. The harness must not bypass the guard — that would invalidate the "production cascade" claim.

**4.11 — Provenance:** git SHA, config/policy/prompt hashes, model + tokenizer revisions, image digest, vLLM/CUDA/driver/GPU versions, RunPod pod roles, price provenance, OpenRouter served model + upstream + ZDR eligibility snapshot. Resume only from per-scenario/per-level checkpoints; never discard adverse cases.

### Architectural decisions
- **AD-4.1** The benchmark imports the **shared** `evaluate_sar_quality`. No second implementation.
- **AD-4.2** Raw AWQ-vs-BF16 stays the only equal-hardware quantization comparison, sequential on one GPU. Co-residency (5.20 + 14.25 = 19.45 GiB on a 24 GB 4090) would starve KV cache and distort every latency number.
- **AD-4.3** Cascade-vs-BF16 is an **architecture** comparison on two provisioned GPUs. GPU-hours/case and aggregate memory are reported so cascade throughput is never presented as a free same-resource gain.
- **AD-4.4** Claims follow measurements. If −63.5% / −36.3% / +60.8% do not reproduce, the **measured** values ship and the resume line changes. The report must say which comparison each figure describes.
- **AD-4.5** Performance deltas alone do not fail the release. Quality, reproducibility, accounting, and truthful reporting do.

### Execution gates
Provider-free validation → RunPod plan → **explicit approval before creating either paid endpoint** → AWQ smoke → both-stage smoke → 40-case dev pilot + projection → budget admission → **explicit approval for the full matrix** → export → ledger reconciliation → teardown approval → read-only verification both pods and volumes are gone.

### Tests and validation
Extend `tests/unit/test_vllm_bench_*.py` for scenario/attempt accounting, resumed checkpoints, two-endpoint telemetry, and partial-endpoint failure, against fake servers (`make vllm-bench-test`, ≥90% branch). Mechanical recomputation tests for every headline, percentile, rate, memory delta, and cost. Publication validation rejects missing cases, mismatched hashes, unequal policies, absent telemetry, or authored headline numbers.

### Acceptance criteria
- Replay pilot committed **before** any paid run; live escalation rate within a stated tolerance of it (divergence is a finding, not a failure).
- Every scenario completes with `error_rate == 0`, GPU telemetry present, token drift ≤ 0.05.
- No serving errors omitted; no failed case selectively rerun.
- **Constrained cascade achieves `reference_validity == 1.0`** — the criterion v1 failed.
- Cascade final quality pass rate ≥ BF16-alone and ≥ 0.99.
- Raw AWQ retains the ≥50% weight-memory acceptance floor.
- Headline mechanically includes memory, throughput, **and p95**.
- Old percentages labelled "reproduced" only if newly derived values match within declared rounding tolerance; otherwise README, claims register, and resume wording take the new values.
- Ledger row complete, teardown verified, `runpod-gpu-verify-clean` clean, `ledger-check` green.

### Verification record — Phase 4 executed 2026-09-16 (branch `release/0.5.0`)

**Executed, zero spend.** `make vllm-bench-cascade-pilot` replays the shipped `SarQualityGate`
over all 1000 persisted cases of `vllm-bench-f810b57a7b8ae05a` under the committed
`sar_quality.replay_gate` policy (`sar-gate-replay-v1`) and composes them through the same
`compose_cases`/`cascade_metrics` a live scenario uses. At concurrency 32 it reproduces the
admission projection this release was scoped on:

| Metric @ c32 | Projected in §Context | Replayed from the run |
|---|---:|---:|
| AWQ gate pass | 73.9% | 73.6% |
| BF16 gate pass | 94.1% | 93.8% |
| Escalation rate | 26.1% | 26.4% |
| Cascade final pass | 96.1% | 95.8% |
| Both tiers fail | 3.9% | 4.2% |
| Cascade p95 | 28,930 ms (+48.9%) | 28,930 ms (+48.9%) |
| GPU time vs BF16 | −10.5% | −10.3% |

The small residual differences are the gate, not the arithmetic: the shipped policy also rejects
duplicated citations, which the pre-implementation analysis did not. Every terminal failure is a
citation fault (citation_duplicated × 1, citation_fabricated × 39, no_citations × 3).

**Matrix sized and costed from that replay.** Scenarios 1–2 keep three levels on one endpoint and
3–5 run c32 on two, per 4.4. Projected 7.14 endpoint-hours → $5.28, or $6.87 with the 30%
admission margin, against the $18.00 `gpu_benchmark` allocation → `projection_within_allocation`.
Hours are inflated by the overhead the prior session actually billed (6.046 billed ÷ 3.929
measured = ×1.539), because boot, weight download, smoke, and teardown are paid time no
measurement window contains. The artifact is committed at
`docs/reference/benchmarks/vllm-cascade-replay-pilot.json` and carries the source run id, so the
ledger's published-report coverage sees it.

**Applied.** The 4.6 paired reserve draw (`gpu_benchmark` $10.00 → $18.00, `reserve` $25.00 →
$17.00; ceiling unchanged at $75.00, `ledger-check` green); the 4.7 governance fixes (SKILL.md now
names the live `runpod-gpu-*` targets and the real `gpu_benchmark` allocation, `.claude/settings.json`
ask-gates the billable `runpod-gpu-{up,down,start,stop,sync}` and drops the dead `gpu-bench-*`
entries, Codex mirror regenerated); protocol v2 with `request.constrained_decoding`, a `cascade`
scenario matrix, and a `protocol_lineage` entry that keeps the published v1 report hash-bound to
the config it was produced under (AD-1.3); the v2 mechanical headline now carries p95. Scenarios
drive the production `QualityGatedSarDrafter` through `build_sar_drafter` over a named SAR profile
and record ONE MEASUREMENT PER ATTEMPT — stage, gate verdict and reasons, connection, served model,
policy hash, tokens, and provider cost — so stage mix, escalation, per-stage accounting, GPU-hours
per case, and the two-endpoint aggregate footprint are all derived rather than asserted. The RunPod
operator is keyed by run AND endpoint role, so two endpoints get independent Pods, sessions,
watchdogs, telemetry, and `verify-clean` evidence.

**State when this verification record was first committed.** No Pod had yet been created and the
live gates remained owner-gated. That state was superseded by the approved 2026-09-17 execution
below; it is retained only to explain why the replay artifact predates the paid evidence.

### Live validation addendum — 2026-09-17

The final session `vllm-bench-ba468433f6fd893a`, bound to commit
`aaf903114131f74e8b2bffc859427b1ec511d896`, passed the infrastructure/provenance failure that
stopped the previous attempt. Both pinned models served successfully, both roles recorded their
own startup logs, GPU telemetry, immutable driver identity, token usage, and zero serving errors.
The client ran on the AWQ Pod and reached loopback-only BF16 through a private SSH tunnel; this
topology is part of the measured method.

| Live checkpoint | Result |
|---|---|
| Raw smoke, `vllm-bench-cf06c21fa6cb425d` | 32/32 generations across AWQ/BF16 c1/c2; zero serving errors; token usage and telemetry complete |
| Same-GPU development, `vllm-bench-c041bc70ff0d4de5` | 40 cases per arm at c32 on driver `580.159.04`; AWQ weight memory −63.5%, request throughput +47.2%, p95 −25.7%; AWQ schema/reference validity 1.0, BF16 aggregate schema/reference validity 0.95 |
| Production constrained cascade, `vllm-bench-3133fb4b74e9a053` | 8/8 escalated AWQ→BF16; 0/8 final pass; 16 attempts, zero serving errors, 16/16 token usage, 1,309 telemetry samples |

The cascade divergence is material: replay predicted 26.4% escalation and 95.8% final pass, while
the live constrained smoke observed 100% escalation and 0% final pass. Gate reasons were
`unmapped_narrative_fact` ×16, `asserted_fact_mismatch` ×13,
`claim_missing_evidence` ×1, and `evidence_ref_unresolved` ×1. This is adverse live evidence, not a
transport failure and not a selectively rerun sample.

Budget admission also failed independently of quality. The two declared constrained scenarios
(`awq-bf16` and `awq-bf16-external`) share the measured self-hosted path. Scaling the smoke's
captured c32 endpoint occupancy to 2×1,000 cases projected $23.130794 before session overhead,
the external API stage, or scenarios 1–3. The committed 30% margin raises that strict lower bound
to $30.070032, so `scripts/experiment_budget.py admit --allocation gpu_benchmark` returned
`projection_exceeds_allocation` against $28.00. The full matrix was therefore correctly not run.

The session consumed 1.523099 summed endpoint-hours (about $1.127093 pending provider settlement).
Both Pods and matching volumes were deleted and independently verified absent. The temporary
production `llm_daily_budget_usd` raise was restored to $0.25. Phase 4's live execution is complete
as a **failed acceptance test**: it supports the 40-case raw-development figures above, but it does
not support the original full-matrix or gated-cascade resume claim. Phase 5 may remediate and
re-admit the benchmark; it must not publish the replay projection as though this live result passed.

### Phase 4 remediation protocol v3 — pre-registered 2026-09-17

The owner directed that Phase 4 reach acceptance rather than close as a failed experiment. The
retained live and raw-smoke artifacts isolate a contract failure, not a reason to lower the gate:
prompt v2 asked the model to trace objective prose through asserted facts, while the response
schema closed only citation ids. It still allowed invented evidence refs, rounded ref/value pairs,
omitted core claims, and omitted FinCEN sections. The 7B models therefore produced structurally
valid JSON that the deterministic production gate correctly rejected.

The remediation is frozen before another paid generation:

1. Prompt `v3@3.0.0` requires concise prose, one ordered core-fact claim, exact display values, and
   no numeric restatement of regulation prose or model-driver values.
2. The constrained schema closes citation ids, evidence refs, and every asserted ref/value pair;
   requires the eight core transaction/risk facts in `claims[0]`; and structurally requires the six
   ordered FinCEN sections. The gate remains unchanged except that a date-only rendering of an
   already-asserted instant is accepted as a truthful loss of precision.
3. Runtime policy becomes `sar-gate-v2`, benchmark protocol becomes `vllm-sar-bench-v3`, and the
   exact v2 config hash enters `protocol_lineage`. Prior adverse evidence remains immutable.
4. The self-hosted completion cap becomes 1,024 tokens. This is a pre-run latency/cost bound, not a
   result-driven retry; truncation remains a hard gate failure.
5. Protocol-v2 case artifacts are regenerated because prompt and config hashes changed. Their
   available evidence refs come from the production catalog rather than the obsolete generic
   fixture refs.

Acceptance thresholds do not move: constrained reference validity `1.0`; final cascade pass at
least `0.99` and no worse than BF16-alone; zero omitted serving errors; token drift at most `0.05`;
AWQ weight-memory reduction at least `0.50`; complete telemetry and provenance. Execute smoke first,
then development-40 and a fresh cost admission. Do not run the full matrix if the corrected pilot
still fails quality or admission, and do not retain old performance percentages unless reproduced.

### Phase 4 remediation protocol v4 — pre-registered after v3 smoke 2026-09-17

The paid v3 AWQ smoke completed 24 generations with zero serving errors, complete token usage,
provenance, and telemetry, but the production gate rejected all 24 as `schema_invalid`. A retained
diagnostic completion showed the unconstrained prompt abbreviated `assertedFacts` into bare strings,
omitted required claim fields, and rendered `sections` as an object. The constrained endpoint then
returned the exact vLLM 0.10.2 provider error `Unimplemented keys: ["uniqueItems"]`. This is a
provider-contract defect, not evidence for relaxing the gate.

Protocol v4 makes only two generation-contract changes before the next paid run:

1. Prompt `v4@4.0.0` states the complete claim, asserted-fact, and section shapes; requires exactly
   one core claim; and distinguishes the catalog's canonical `value` column from prose display text.
2. The response schema omits `uniqueItems`, which vLLM 0.10.2 cannot compile. Duplicate citations
   remain prohibited by the prompt and deterministically rejected by the unchanged production gate.

A live diagnostic request using that compatible constrained schema produced a Pydantic-valid draft
that passed `sar-gate-v2` with citation precision `1.0`, no mismatched facts, no unmapped narrative
facts, and all six FinCEN elements. That diagnostic is compatibility evidence only; protocol v4 must
still repeat smoke, development, admission, and the full matrix. All acceptance thresholds above
remain unchanged, and v3 enters `protocol_lineage` under config hash
`4f9388d51ec47b0ef1f806e8784e56afcde3c67a66c42ba7d14bdcebdf4f5534`.

### Phase 4 remediation protocol v4 — live result 2026-09-17

Protocol v4 ran from commit `398cdacf77de0708f2cc841e3795a47a2d208deb` against corpus SHA
`abca4898c5101e62cbff9348c37eb2e7712bc6970d3e4aaf4d5256f399612447`, config SHA
`47349665261b5c0f893b156227003e7192d40928df78bb674fb1796186f6c8cf`, and prompt SHA
`254b765cbeaf52deb5296583661e572e3a03e891d5f6b168aabc3999833e116e`.

- AWQ and BF16 unconstrained smokes each completed 24/24 live generations with zero serving
  errors, complete token accounting, and GPU telemetry, but each passed the production gate 0/24.
- The real constrained `awq-bf16` route passed 8/8 cases at AWQ, so no case escalated and BF16
  received no generation request. Reference validity was 1.0 under the unchanged production gate.
- The constrained p95 was 522.8445 seconds (p50 499.5075 seconds), so v4 failed the latency
  objective even though it fixed quality. The large case-specific grammar, not model service
  availability, was the measured bottleneck.
- AWQ model-weight memory was 5.2036 GiB versus BF16 14.2488 GiB, preserving the 63.5% reduction.
- The retained constrained artifact SHA is
  `72d7a5dddcc6de6e0238344543732bf758b29d72f5dfc7368cc3ffbaf88be595`; the BF16 raw artifact
  SHA is `fea1acadc8759bf76933e05fed0af21f5d2a9d77541ac926862c5d7a82a57218`.
- The session used 0.906908 summed endpoint-hours (estimated $0.671112 pending settlement).
  Both Pods and matching volumes were deleted and independently verified absent.

Protocol v4 therefore closes the live quality uncertainty but does not satisfy Phase 4 acceptance:
its latency cannot support a cascade-latency reduction claim, and development-40 plus the admitted
1,000-case matrix remain unrun under a viable constrained contract.

### Phase 4 remediation protocol v5 — pre-registered 2026-09-17

The owner approved a compact generation boundary intended to preserve v4's quality result while
removing its grammar bottleneck. This is a versioned protocol change made before another paid Pod:

1. The model emits only `subject`, `narrative`, one `claimStatement`, six named section bodies, and
   citation ids. It does not emit evidence refs, asserted fact objects, section headings, or the
   recommended action.
2. The backend deterministically hydrates evidence refs and canonical asserted values from the
   trusted case catalog, supplies the fixed six FinCEN headings and human-review action, then runs
   the unchanged production `SarQualityGate`. Citation ids remain a closed enum and rejected prose
   remains buffered from clients.
3. Prompt `v5@5.0.0`, benchmark protocol `vllm-sar-bench-v5`, and a 512-token completion cap replace
   v4. The v4 config SHA enters `protocol_lineage`; all v4 evidence remains immutable.
4. Two one-stage constrained controls are added: `awq-constrained` is the first paid canary, and
   `bf16-constrained` provides an equal-contract latency baseline. BF16 is not provisioned until the
   AWQ-only smoke passes quality and demonstrates a material latency improvement over v4.
5. Only after the AWQ canary passes may the real `awq-bf16` smoke run. It must then pass the same
   reference-validity/final-quality thresholds before development-40 and a fresh cost admission.
   The 1,000-case matrix remains prohibited until admission passes.

The provider-free gate for this design is 134/134 focused tests plus 135/135 canonical benchmark
tests at 91.17% branch coverage and `make vllm-bench-validate`. These checks passed before corpus
regeneration. The deterministic v5 corpus contains 1,070 cases (1,000 measured, 40 development,
10 warm-up, 20 abstention) and binds config SHA
`57df143fde48dd7cfee6c14d366ea261d8bfa36c949e1be5ad5cbf0d7d70f169`, prompt SHA
`7041a9a4f2b5f985cf9ae7861b671d11432bd22c218a7bbb39094d1451cef77d`, and artifact SHA
`2e1df4db3099fcd411158fd18d1a6bcb9cf69bf1dadef8138d68712a049376e2`.
Live latency, escalation rate, and throughput remain unknown until the new smoke; no v4
performance percentage is carried forward as a v5 result.

### Phase 4 remediation protocol v5 — live canary result 2026-09-17

AWQ-only session `vllm-bench-82cddfec5c550250` ran from commit
`c905618c1b2b22fb2210eef54368aa63394e1d60` and the pre-registered v5 corpus. The constrained
`awq-constrained` c32 smoke completed eight real generations with zero serving errors, complete
tokens/provenance/telemetry, and 8/8 production-gate pass, but p95 remained 441.57035 seconds.
Average GPU utilization was only 2.44%, directly implicating guided schema decoding rather than
model capacity. V5 improved v4's 522.8445-second p95 by 15.5%, but still failed latency acceptance.

The same compact envelope through the unconstrained `awq-raw` route then produced eight real c1
generations: 8/8 gate pass, zero errors, and p95 2.616 seconds. This supports the next architecture
decision: keep deterministic hydration and the unchanged gate, but let gate rejection—not the
vLLM 0.10.2 grammar engine—trigger BF16 escalation. It does not yet support a concurrency claim.

The attempted c8/c32 controls were served from the drafter's in-memory cache after c1 and are
explicitly invalid. The run is retained under artifact SHA
`34634cf87b3e4c487dc931708237ed0544824fa5f7b29866eeb9f83dcfbf5b18`; those two checkpoints must
never enter a report. The harness now constructs a fresh production drafter/cache per concurrency
level and has regression coverage proving resume builds no drafter for completed levels. Repeat
AWQ smoke from a new commit before BF16 provisioning. The session estimated $0.221947 and ended
with zero matching Pods or volumes.

### Phase 4 remediation protocol v5 — cache-isolated development and admission 2026-09-17

Commit `e0965892abe262727c6d7c549a3c06e45c2753a9` repeated the unconstrained route with one
fresh production drafter/cache per level. Session `vllm-bench-39007a09a3c83bcb` produced 24/24
AWQ and 24/24 BF16 smoke generations across c1/c8/c32 with zero serving errors. At smoke c32,
AWQ p95 was 4.53185 seconds versus 8.0249 for BF16, request throughput was 1.7523 versus 0.9845,
and the two-endpoint cascade passed 8/8 at 4.38-second p95.

Development run `vllm-bench-2499598f2617bc6b` then completed the same six raw levels plus the
40-case `awq-bf16-unconstrained` c32 production cascade. The cascade passed 40/40 with zero
serving errors, p95 13.4459 seconds, reference validity 1.0, and zero escalation because AWQ
passed every c32 case. Equal-contract AWQ c32 p95 was 13.7104 seconds versus 16.62465 for BF16
(17.5% lower), and request throughput was 2.3615 versus 1.8526 requests/second (27.5% higher).
The absence of a development escalation is disclosed; only the 1,000-case cascade may establish a
non-zero live escalation rate.

Scaling the seven passed levels to 1,000 cases projects $2.210435 of compute, or $2.873566 with
the required 30% margin. `experiment_budget.py admit` returned `projection_within_allocation`.
The constrained-decoding scenarios are not expanded because their live canary failed latency
acceptance at 441.57035-second p95; they remain adverse evidence rather than part of the admitted
full matrix. The pilot consumed 0.841213 summed endpoint-hours (~$0.622497 pending settlement),
and both Pods plus volumes were verified absent before the full-run ledger row opened.

### Phase 4 remediation protocol v5 — full live result 2026-09-18

Run `vllm-bench-be12675628805a53`, bound to commit
`615285e0e63f85eb2ee0661ae21bd36a4cbf8f8d`, completed all seven admitted levels: AWQ and
BF16 at c1/c8/c32 plus the production `awq-bf16-unconstrained` cascade at c32, each over the full
1,000-case measured population. The three coordinator/role exports are byte-identical at SHA-256
`4ba9307f1a2c8bbe059d2e402f94e2c4e16f61e251a303fea297976da908fd6a`.

The production cascade made 1,089 model calls with zero serving errors. AWQ served 905 cases,
92 escalated and passed at BF16, and three evidence-free cases stopped at preflight without a model
call. Final pass was 997/1,000 (99.7%) versus 995/1,000 (99.5%) for BF16 alone; accepted-output
reference validity was 1.0 and token-accounting drift was zero. Cascade case p50/p95/p99 was
11.0085/17.18425/19.31831 seconds. Against BF16's 13.225-second p50 and 16.1639-second p95, the
cascade improved typical latency 16.8% but added 6.3% at the tail. This is a passed quality-first
cascade result, not evidence for a p95 reduction.

At full c32, raw AWQ p95 was 16.22815 seconds versus 16.1639 for BF16 (+0.4%), and request
throughput was 2.4594 versus 2.3987 requests/second (+2.5%). The 40-case development performance
deltas did not reproduce. These raw levels used the same RTX 4090 SKU/image/protocol but separate
physical hosts with different driver revisions, so the small full-run raw deltas are diagnostic,
not a new equal-host headline; the immutable v1 report remains the equal-hardware quantization
evidence. AWQ parsed weight memory remained 63.5% lower. The two-endpoint cascade peaked at
43,540 MiB aggregate device memory and used 76.2% more provisioned GPU-hours per case than BF16.

The approximately 5.006 summed endpoint-hours estimate $3.70 pending settlement, above the
$2.873566 admission-with-margin projection because the model omitted idle second-endpoint time
during sequential raw levels, but within the $37 allocation. Both Pods and matching volumes were
deleted after export and independently verified absent. The post-run composer now treats preflight
stops as zero-model-call cases, evaluates quality only on analyst-visible accepted drafts, and has
139 passing benchmark tests at 91.26% branch coverage. Phase 4 live execution is complete. The
scenario-shaped publication, disclosures, and resume/README wording are Phase 5 work.

### Dependencies
Phases 2 and 3.

---

## Phase 5 — Cleanup and release readiness

### Objective
Remove what the new architecture supersedes, publish accurate evidence, verify deployment compatibility, and prepare 0.5.0.

### Carried into Phase 5 from the Phase 3 and Phase 4 risk registers

These are decided or closed here; none of them blocks Phase 4's committable scope. Items already
closed are retained so the audit trail is not re-opened; the remaining publication and product
decisions belong to Phase 5.

| # | Item | Source | What Phase 5 must do |
|---|---|---|---|
| 1 | SSE reconnect over an escalated run | P3 risk 1 | **Closed.** `test_pipeline_sar_cascade.py::test_a_reconnecting_client_replays_the_stage_decisions_without_the_rejected_draft` drives `_event_stream(after_seq=0)` over a real escalated run, asserts the full stage-frame order, asserts the reason code is present and the rejected narrative is not, and resumes from `after_seq=8`. No further work. |
| 2 | Cascade suites relied on `MockTransport` alone | P3 risk 2 | **Closed.** `test_sar_cascade_live.py` and `test_sar_cascade_governance.py` both carry an autouse `_deny_sockets` fixture patching `socket.socket.connect`, the same denial `test_model_egress.py` uses, so AD-3.1 binds literally. No further work. |
| 3 | `escalation_tier` is reconstructable but not surfaced | P3 risk 3 | Decide whether the analyst surface needs the field. It is persisted on the draft and derivable from `sar_generation_attempts`; if the UI is to show "this draft came from tier 2", expose it through the alert/SAR read model and the frontend type rather than making clients join attempts. If not, record the decision in ADR-030 so it is not re-litigated. |
| 4 | Tier-3 pilot set is 88 both-tier failures plus 12 AWQ-only | P3 risk 4 | The composition is declared in `config/experiments/sar-tier3-pilot.yaml` and asserted by `test_sar_tier3_pilot.py`. Before the pilot is RUN, the owner confirms the top-up is acceptable, since the both-tier population is genuinely smaller than the fixed set of 100. Record the confirmation in the ledger row for the pilot's spend. |
| 5 | `citation_recall_min` bound to a recorded number | P3 risk 5 | **Closed in Phase 4.** The committed replay pilot now derives `armRecallMean` and `armPassRate` from the full 1,000-case run, and `test_citation_quality.py` plus `test_sar_cascade_quality.py` assert the corpus's provenance block equals those derived values. The floor still binds at the population where it was measured; nothing is typed into the fixture unchecked. |
| 6 | Reserve draw applied with no ledger row open | P4 risk 1 | **Closed.** Every paid session has a ledger row. Final full run `vllm-bench-be12675628805a53` is reconciled at approximately 5.006 endpoint-hours pending provider billing, with clean teardown. |
| 7 | Live escalation may diverge from the replayed 26.4% | P4 risk 2 | **Closed as a measured finding.** Protocol v5 full live escalation was 9.2%, final pass was 99.7%, and the earlier v2/v4 adverse results remain retained. Publication must use the live v5 rate, not the replay projection. |
| 8 | Two-endpoint p95 can read as a same-resource loss/gain | P4 risk 4 | In the published cascade report, p95 must appear next to `gpuHoursPerCase` and `aggregateMemoryPeakMib`, and the report must state which comparison each figure describes (AD-4.3, AD-4.4). |
| 9 | `awq-bf16-unconstrained` exists only to isolate scenario 3 vs 4 | P4 risk 5 | Decide its disposition: keep it as a shipped production profile, or mark it benchmark-only in `config/llm/sar-vllm.yml` and the ADR. Do not delete it before the constrained-versus-unconstrained comparison is published. |
| 10 | Run-level provenance still partly manual | P4 gap | The live manifests capture `gitCommit`, role-specific driver identity, connection, served model, policy hash, tokens, and provider cost. Still to capture at run time: the CUDA version and the OpenRouter ZDR eligibility snapshot taken at readiness. Add both to the published report's provenance block. |
| 11 | No report builder for v2+ scenarios | P4 gap | `build_report` is still the two-arm builder. Publishing the gated-cascade report needs a scenario-shaped report, acceptance checks, and Markdown rendering built on the level metrics Phase 4 already derives (`cascade`, `stageTotals`, `telemetryByRole`, `gpuHoursPerCase`, `aggregateMemoryPeakMib`). The v1 report and its lineage entry stay untouched. |
| 13 | RunPod REST v1 is drifting under us | P4 live run | Three breakages surfaced on 2026-09-17: `volumeEncrypted` rejected on create, `publicIp: ""` before placement crashing response parsing AFTER the Pod existed (a billing orphan), and encryption no longer reported at all. Each is patched on `/v1`, but the v2 shape nests GPU and mount settings entirely differently (`gpu.{id,count}`, `mounts.persistent.{size,path}`) and drops `interruptible`, `locked`, `computeType`, `gpuTypePriority` and `minDownloadMbps`. Migrate the operator to REST v2 — the `runpod:migrate` skill inventories and rewrites — before the next paid session, or expect the next drift to land mid-run. |
| 14 | Volume encryption is no longer obtainable | P4 live run | The frozen contract required an encrypted Pod volume AND its verification; the provider supplies neither. Accepted for this run because the corpus is the public IBM AML synthetic dataset, with the observed value recorded on each session. ADR-030 must state this as a judged exception with reconsideration criteria, and the published report must disclose it. Rotating `VLLM_API_KEY` after teardown is prudent: it is written to an unencrypted volume at `/workspace/.fraudlens/vllm-api-key`. |
| 12 | `prod.yaml` app-path budget | P4 4.10 | **Closed for this session.** The approved temporary ceiling was raised to $22.00 for the production-path run and restored to $0.25 immediately after teardown; release-checklist item 18 keeps verifying the restored value. |
| 15 | Live production cascade fails quality and cost admission | P4 live validation | **Closed by protocol v5.** Smoke, development, admission, and the full matrix completed; final pass was 99.7%. The earlier 0/8 constrained result remains adverse evidence and the published report must explain why guided decoding was excluded after its latency canary. |

### Changes required

**Dead code**
- Delete `SarInput.rag_context` and producers; fix the stale docstring at `sar/prompt.py:9`
- Delete `fraudlens_core.types.TransactionSummary` and its single test
- Delete the empty `config/quality/` directory
- Remove the direct model-only benchmark load runner and post-hoc validator **after** scenario-parity tests pass
- Remove confirmed-unused frontend exports (exports only, not used implementations)
- Remove test-only private seams (`LlmClient._resolve_model`, `SarPromptTemplate._split_front_matter`) if the new tests no longer need them

**Configuration consolidation**
Single ownership: model cards/prices → catalog; connections/privacy → provider registry; stages/profiles → SAR config; runtime **and** aggregate quality thresholds → `config/quality.yaml`; workload/measurement methodology → benchmark config; infrastructure/prices → RunPod + experiment config. Lift `e2e.py` `_MAX_CASES`/`_MAX_CONCURRENCY` (restated 3× each) into config. Align `LlmSettings` `extra="ignore"` with `AppSettings` `extra="forbid"` so a typo'd `FRAUDLENS_LLM_*` fails loudly.

**Documentation**
- **New ADR-030: quality-gated SAR model cascade** — deterministic gate not LLM judge; quality-tier vs transport-fallback separation; one attempt per tier; explicit failure over degradation; named connections; constrained decoding; pilot-based external-model selection with the rejected alternatives; reconsideration criteria. Retain ADR-020 as the raw-AWQ decision and link its failed quality result forward.
- Publish the gated-cascade report + machine-readable artifact; leave v1 immutable.
- README distinguishes three things that are currently blurred: the live Container Apps/Vercel deployment; the reproducible **ephemeral** AKS/HPA demonstration; the ephemeral two-endpoint RunPod benchmark.
- **Disclose the AKS load caveat**: 1,147 / 783,498 requests succeeded (~99.85% rate-limited). Add it to the report's `disclosures` or re-run within the rate limit, and strengthen the validator beyond `succeeded > 0`. This is the most defensible criticism of an otherwise strong artifact.
- `AGENTS.md:265` — remove the non-existent `gpu-bench` Terraform root; `docs/runbooks/vllm-benchmark.md:24` and `runpod_gpu/planning.py:8` — "$25" → real allocation; add `runId` to `k8s-hpa-scaling.json` (currently invisible to ledger coverage).
- Document the OpenRouter ZDR posture and route-revalidation behaviour.
- Update architecture Mermaid diagrams, quality-gate docs, claims register, interview guide, handoff.
- `make docs` to regenerate AUTOGEN regions, OpenAPI, and the Codex skill mirror.

**Version + deployment parity**
Bump **all seven** lockstep locations 0.4.0 → 0.5.0; `make release-gate`. Verify Container Apps and AKS can select the same SAR profile contract, and that **inactive RunPod stages require no secrets and do not fail readiness**. Do not modify AKS Terraform unless a compatibility test proves an actual defect.

### Release checklist — 0.5.0

| # | Item | Gate |
|---|---|---|
| 1 | Dated plan in `plans/` + ADR-030 accepted | blocking |
| 2 | Prompt v3 and quality-policy versions frozen and hashed | blocking |
| 3 | `make pre-pr` green (`deploy-identity-check fmt docs ci`) | blocking |
| 4 | `make quality-gates` green; citation gate proven non-vacuous | blocking |
| 5 | No `status=draft` without `quality.passed=true`; no ungated draft approvable | blocking |
| 6 | Attempts/cost/provenance tenant-scoped and queryable; migration up+down | blocking |
| 7 | `make file-length-check` — no file ≥ 500 (`agents/graph.py` has 6 lines headroom) | blocking |
| 8 | `no-hardcoding-check`, `secrets-scan`, `demo-literals-check`, `tenancy-check` green | blocking |
| 9 | `make deadcode` reviewed; superseded code deleted | blocking |
| 10 | `make docs-check` green (skill mirror byte-identical) | blocking |
| 11 | Two-endpoint RunPod smoke + 100-case pilot pass; external model frozen | blocking |
| 12 | Full matrix completes and validates; headlines mechanically derived | blocking |
| 13 | v1 raw benchmark evidence unchanged | blocking |
| 14 | Ledger complete, teardown verified, both pods + volumes verified deleted | blocking |
| 15 | AKS load caveat disclosed or re-measured; `k8s-validate` green | blocking |
| 16 | Claims register updated; no claim ahead of evidence | blocking |
| 16a | Carried risk-register items 3, 4, 9 decided and recorded (ADR-030 or ledger) | blocking |
| 16b | Report provenance carries CUDA version and the ZDR eligibility snapshot | blocking |
| 17 | Versions at 0.5.0; CHANGELOG `[0.5.0]` via `git-cliff` | blocking |
| 18 | `prod.yaml` daily budget restored | blocking |
| 19 | `make attribution-check` over every local ref | blocking |
| 20 | Tag `v0.5.0` from a CI-green commit — **explicit human instruction only** | blocking |

### Acceptance criteria
One inference path, one quality evaluator, one model/profile source, one benchmark measurement implementation. No parallel old/new path. A single-model config still produces exactly today's behaviour. No secrets, URLs, model ids, thresholds, or concurrency values hardcoded in source. No paid resources remain.

### Dependencies
Phases 2, 3, 4.

---

## Verification

**Per phase, locally, no GPU:**
```bash
make pre-pr
make quality-gates
uv run pytest tests/unit/test_sar_quality_gate.py tests/unit/test_sar_drafter_gated.py -q
uv run pytest tests/integration/test_sar_cascade_live.py tests/integration/test_pipeline_sar_cascade.py -q
make vllm-bench-test && make vllm-bench-validate
```

**Cascade end-to-end without a GPU** — run the backend in `live` mode against `CapturedOpenAiEndpoint`, POST a transaction scoring above the alert threshold, and confirm on the SSE stream: `sar.stage.started` (awq) → `sar.stage.rejected` with reason codes and **no draft text** → `sar.escalated` → `sar.stage.started` (bf16) → `sar.token` events → `sar.completed` with `escalationTier: 1` and a passing `qualityGate`.

**Replay pilot (free, real data):**
```bash
uv run python scripts/benchmark_vllm.py cascade-pilot --run vllm-bench-f810b57a7b8ae05a
```
Expect ≈26% escalation and ≈96% final pass at c32. A large divergence means the gate does not match the analysis and must be reconciled **before** spending.

**Live run** — only after owner approval of the reserve draw and the pilot-derived matrix, following the corrected `benchmark-vllm` skill: smoke → both-stage smoke → dev-40 → `admit` → full → export → reconcile → teardown → `verify-clean`.

**Drift check:**
```bash
drift-check plans/2026-09-16-sar-quality-gate-cascade-and-gated-benchmark.md all
```

---

## Risks

| Risk | Mitigation |
|---|---|
| Constrained decoding raises per-token latency enough to erode the AWQ advantage | Scenarios 3 vs 4 isolate exactly this; if so, ship unconstrained + gate and report honestly |
| Asserted-fact matching is too strict and rejects good drafts | The clean-draft false-positive threshold (≤0.05) is a release-blocking gate; normalisation rules are explicit, total, and unit-tested before the corpus is trusted |
| Live escalation rate ≫ 26%, cascade p95 stays poor | Replay pilot detects before spend; fallback is arm-level claims plus the GPU-time and quality wins |
| Two-endpoint matrix exceeds the raised allocation | Matrix shrinks to c32-only for cascade scenarios; allocation does not silently grow |
| `agents/graph.py` (6 lines headroom) or `cases_ibm.py` (37) breach the cap | Cascade logic goes in new modules; use the `split-module` skill if forced |
| OpenRouter ZDR route eligibility changes between selection and run | Revalidate at readiness and record the eligibility snapshot in report provenance |
| Tier 3 blocked by egress for non-synthetic data | Correct and intended; cascade fails explicitly. Tier 3 serves synthetic-class cases only |
| Reserve draw not approved | Phase 4 degrades to the replay-composed result, clearly labelled; Phases 2/3/5 unaffected |
