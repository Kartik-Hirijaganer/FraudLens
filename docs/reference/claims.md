# Evidence-backed Claim Register

This register prevents portfolio and README language from running ahead of repository evidence.
Statuses mean: **implemented** = code/config exists; **tested** = deterministic automated evidence;
**demonstrated** = a published, reproducible result; **planned** = not yet a present-tense claim.
A demonstrated row always links to its committed evidence.

## Current README claims

| Claim | Status | Evidence |
| --- | --- | --- |
| FraudLens provides an explainable, tenant-safe AML investigation path. | tested | [Tenant boundary tests](../../tests/integration/test_repositories.py) and [pipeline tests](../../tests/unit/test_pipeline.py) |
| The repository uses no real PHI, stores masked demo data, validates JWT tenant identity, and keeps secrets out of source. | tested | [Adversarial security tests](../../tests/security/test_agent_adversarial_tool_safety.py), [auth tests](../../tests/integration/test_api_v1.py), and [secret guard](../../scripts/check_no_secrets.py) |
| Transaction ingest supports single, batch, and CSV paths with tenant-scoped pagination. | tested | [Transaction API tests](../../tests/integration/test_transactions_api.py) |
| Hybrid scoring combines deterministic rules with calibrated XGBoost operating points. | tested | [Pipeline tests](../../tests/unit/test_pipeline.py) and [model training tests](../../tests/integration/test_train_model.py) |
| Decisions retain rule hits and SHAP contributions. | tested | [Scorer tests](../../tests/unit/test_scoring_scorer.py) and [explainer tests](../../tests/unit/test_scoring_explainer.py) |
| Below-threshold investigations short-circuit before RAG and SAR drafting. | tested | [Pipeline behavioral tests](../../tests/unit/test_pipeline.py) |
| Regulatory RAG uses versioned FinCEN/BSA evidence with deterministic local and guarded live embeddings. | tested | [RAG ingest tests](../../tests/unit/test_rag_ingest.py) and [live embedder tests](../../tests/unit/test_rag_embedder_live.py) |
| SAR drafting is masked, schema-bound, citation-grounded, budgeted, and replayable through mock/live provider seams. | tested | [SAR contract](../../tests/unit/test_sar_contract.py), [citation tests](../../tests/unit/test_rag_citations.py), and [live drafter integration](../../tests/integration/test_sar_drafter_live.py) |
| Every SAR draft is judged by a deterministic quality gate before it is persisted or streamed; a rejected draft is refused, never silently repaired. | tested | [Quality-gate tests](../../tests/unit/test_sar_quality_gate.py), [gated drafter tests](../../tests/unit/test_sar_drafter_gated.py), and [ADR-030](../architecture/adr/ADR-030-quality-gated-sar-model-cascade.md) |
| A citation-failed draft auto-escalates to the next model tier, and a cascade that exhausts every tier fails explicitly with its reason codes. | demonstrated | [Measured gated-cascade benchmark](benchmarks/vllm-gated-cascade-benchmark.md) — 9.2% of 1,000 cases escalated at concurrency 32 and all 92 were served by BF16 |
| An uncommitted or tampered regulation excerpt cannot reach the model at all: model egress refuses the request rather than fencing the payload. | tested | [Untrusted-input safety gate](../../tests/security/test_input_safety.py) and [ADR-026](../architecture/adr/ADR-026-synthetic-only-model-egress.md) |
| The analyst UI covers dashboards, transaction search, investigations, alert/SAR review, and role-aware navigation. | tested | [Frontend page tests](../../frontend/src/pages) and [API integration tests](../../tests/integration) |
| Model operations support human-gated candidate, shadow, canary, active, rollback, and advisory drift states. | tested | [Model lifecycle tests](../../tests/unit/test_model_lifecycle.py) and [lifecycle API tests](../../tests/integration/test_model_lifecycle_api.py) |
| Operational and review mutations are auditable without logging PHI. | tested | [Audit consistency tests](../../tests/integration/test_audit_consistency.py) and [logging tests](../../tests/unit/test_logging.py) |
| The graph-feature study measures the performance/isolation boundary without live tenant reads. | demonstrated | [Published GFP study](benchmarks/gfp-tenant-isolation-study.md) and [bound data](benchmarks/gfp-tenant-isolation-study.json) |
| The multi-agent SAR study is bounded, synthetic, redacted, and reproducible. | demonstrated | [Published SAR study data](benchmarks/sar-multi-agent-study.json) and [ADR-019](../architecture/adr/ADR-019-multi-agent-sar-drafting.md) |
| Backend and frontend coverage are gated at 90% and local/CI checks share Make targets. | implemented | [Makefile](../../Makefile) and [reusable CI workflow](../../.github/workflows/_ci-reusable.yml) |
| Azure Container Apps, Vercel, and Supabase serve the live application at one public origin, with every deploy job gated behind required production approval. | demonstrated | Live at [fraud-lens-amber.vercel.app](https://fraud-lens-amber.vercel.app) and the [Azure deployment runbook](../runbooks/azure-deploy.md) |
| A dated monthly cost projection is generated from the committed deployment shapes, with budget alerts at two scopes and hard caps that bound the bill. | implemented | [Generated cost model](cost-model.md) and [ADR-029](../architecture/adr/ADR-029-recurring-operational-budget.md) |

## Release 0.3.0 target claims

These are not present-tense claims until their evidence rows can move beyond `planned`.

| Claim | Status | Evidence required |
| --- | --- | --- |
| vLLM + 4-bit AWQ reduces model-weight memory by more than 50% versus BF16 over 1,000 synthetic SAR cases at concurrency 1, 8, and 32. | demonstrated | [Measured benchmark report](benchmarks/vllm-awq-sar-benchmark.md) — 63.5% reduction, re-verified by the [0.5.0 cascade matrix](benchmarks/vllm-gated-cascade-benchmark.md). Weight memory only; it was never a quality-parity claim. |
| Kubernetes manifests include a real HPA and durable worker execution proven on kind. | demonstrated | [Measured HPA and durable-worker evidence](benchmarks/k8s-hpa-scaling.md) |
| The full-data pipeline processes all 68,228,066 downloaded IBM transactions with temporal evaluation and calibration-derived thresholds. | demonstrated | [Published full-data training study](benchmarks/ibm-full-data-training.md) |
| A human-approved, ephemeral AKS session applied the validated Terraform and measured HPA scale-out and durable-worker recovery on the cluster. | demonstrated | [Measured AKS HPA and durability evidence](benchmarks/aks-hpa-scaling.md) and the [AKS runbook](../runbooks/aks-deploy.md) |

## Release 0.5.0 claims

Every row is derived by the published report from run `vllm-bench-be12675628805a53`; none is
authored. Figures are at concurrency 32 over 1,000 IBM-derived synthetic cases unless stated.

| Claim | Status | Evidence |
| --- | --- | --- |
| The gated AWQ→BF16 cascade served 99.7% of cases, above BF16 alone at 99.5% and raw AWQ at 90.9%. | demonstrated | [Gated-cascade benchmark](benchmarks/vllm-gated-cascade-benchmark.md) |
| Raw AWQ terminally failed 85 of 1,000 cases on citation fabrication; the gated cascade and BF16 alone fabricated none. | demonstrated | [Terminal-failure reason codes](benchmarks/vllm-gated-cascade-benchmark.md) |
| Accepted drafts measured reference validity 1.0 on every scenario. | demonstrated | [Accepted-draft quality](benchmarks/vllm-gated-cascade-benchmark.md) |
| The cascade costs 6.3% higher case p95 latency and 76.2% more GPU-hours per case than BF16 alone — **across two endpoints, not one**. | demonstrated | [Labelled comparisons](benchmarks/vllm-gated-cascade-benchmark.md) |
| AWQ is 49.5% faster than BF16 at concurrency 1 but 0.4% slower at concurrency 32; the 63.5% weight-memory reduction holds at every level. | demonstrated | [Labelled comparisons](benchmarks/vllm-gated-cascade-benchmark.md) |
| The gate verdict the production drafter recorded at request time equals the verdict re-derived from persisted output across all 1,092 attempts. | demonstrated | `gate_verdict_parity` in the [published acceptance table](benchmarks/vllm-gated-cascade-benchmark.md) |
| Constrained (guided) decoding was measured and excluded: it passed quality 8/8 but produced a 441.6-second p95 at concurrency 32. | demonstrated | [Report disclosures](benchmarks/vllm-gated-cascade-benchmark.md) and the [experiment ledger](experiments/ledger.md) |
| The external (tier-3) hosted stage ships configured but unmeasured; no 0.5.0 run exercised it. | implemented | [`config/llm/sar-vllm.yml`](../../config/llm/sar-vllm.yml) and [ADR-030](../architecture/adr/ADR-030-quality-gated-sar-model-cascade.md) |
| The AKS scaling load served 1,147 of 783,498 requests (0.15%); it proves HPA scale-out and convergence, **not** sustained throughput. | demonstrated | Disclosed in [AKS HPA evidence](benchmarks/aks-hpa-scaling.md); `validate_evidence` now refuses to publish a sub-95% served share without this disclosure |
