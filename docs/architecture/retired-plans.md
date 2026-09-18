# Retired implementation plans

FraudLens used to start non-trivial work with a dated, phase-based plan under `plans/`. That
directory is retired: **ADRs, runbooks, published benchmark reports, and tests are the maintained
evidence**, and a plan that has landed adds a second, drifting description of the same system.

This file exists so the history stays navigable. Retired plan files are intentionally absent from
the working tree — the removing commit identifies the transition without leaving dead Markdown
links behind.

## Retired plans

| Retired plan | Removing commit | Maintained replacement |
| --- | --- | --- |
| `2026-06-09-tech-stack-foundation-and-workflows.md` | `4cd0e63` | [Makefile](../../Makefile), [architecture](ARCHITECTURE.md), and [ADR index](adr/README.md) |
| `2026-06-12-aml-fraud-detection-system.md` | `ef51c0c` | [Architecture](ARCHITECTURE.md), [reference docs](../reference), and ADR-001 through ADR-016 in the [ADR index](adr/README.md) |
| `2026-07-14-gfp-isolation-gap-visual-study.md` | `ef51c0c` | [ADR-017](adr/ADR-017-graph-feature-serving-boundary.md) and the published [tenant-isolation study](../reference/benchmarks/gfp-tenant-isolation-study.md) |
| `2026-07-25-portfolio-demo-story.md` | Not present in tracked history; dead reference removed in Phase 0 | [ADR-018](adr/ADR-018-portfolio-demo-data-provenance.md) and the [portfolio demo runbook](../runbooks/portfolio-demo.md) |
| `2026-08-17-multi-agent-investigation-and-azure-deployment.md` | `9fac245` | [ADR-019](adr/ADR-019-multi-agent-sar-drafting.md) |
| `2026-09-13-local-pr-check-command.md` | `9fac245` | `make pr-check` in the [Makefile](../../Makefile) and [scripts/check_pr_title.sh](../../scripts/check_pr_title.sh) |
| `2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` | `9fac245` | [ADR-020](adr/ADR-020-vllm-awq-self-hosted-sar-inference.md) through [ADR-028](adr/ADR-028-paid-experiment-governance.md) in the [ADR index](adr/README.md), plus the published [vLLM/AWQ benchmark](../reference/benchmarks/vllm-awq-sar-benchmark.md), [full-data training](../reference/benchmarks/ibm-full-data-training.md), and [HPA scaling](../reference/benchmarks/k8s-hpa-scaling.md) reports |
| `2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md` | `TBD` | [ADR-021's amendment](adr/ADR-021-aks-ephemeral-kubernetes-demonstration.md) and [ADR-029](adr/ADR-029-recurring-operational-budget.md), plus the [Azure deploy runbook](../runbooks/azure-deploy.md), the [cost model](../reference/cost-model.md), and the published [AKS HPA scaling report](../reference/benchmarks/aks-hpa-scaling.md) |
| `2026-09-16-sar-quality-gate-cascade-and-gated-benchmark.md` | Retired with the `plans/` directory | [ADR-030](adr/ADR-030-quality-gated-sar-model-cascade.md), the published [gated-cascade benchmark](../reference/benchmarks/vllm-gated-cascade-benchmark.md), and the [0.5.0 Phase 4 live run](../handoff/0.5.0-phase4-live-run.md) |

## What the 0.4.0 scope became

The five items deferred out of the 0.3 closeout all shipped in 0.5.0, and the sixth shipped with
the Azure deployment work:

1. **Deterministic `SARQualityGate` over ChromaDB-retrieved source spans** — runs on the production
   path before anything is persisted or streamed ([ADR-030](adr/ADR-030-quality-gated-sar-model-cascade.md)).
2. **AWQ first, then regenerate with BF16 or a hosted tier when the gate fails** — done for
   AWQ→BF16, measured at 9.2% escalation with every escalated case served. The hosted GPT-5-mini
   tier ships configured and **unmeasured**; its pilot was never run.
3. **Stream the model, validation, rejection reason, and escalation decision over SSE** — stage
   frames carry the decision and its reason codes, never the rejected narrative, including on
   reconnect.
4. **Publish an AWQ evaluation note explaining the quality gap and the model-cascade pattern** —
   the gap is measured, not asserted: raw AWQ fabricated citations on 85 of 1,000 cases at
   concurrency 32, and BF16 on none.
5. **Ground the validator in FinCEN SAR narrative field requirements** — via the closed evidence
   catalog and asserted-fact matching.
6. **Run the human-approved AKS demonstration** — done 2026-09-16. Session `aks-demo-20260915-01`
   applied the validated Terraform, deployed, captured measured HPA and durability evidence, then
   destroyed the cluster and verified it clean.

AKS wording is set by [ADR-021's amendment](adr/ADR-021-aks-ephemeral-kubernetes-demonstration.md),
and every present-tense claim is capped by the [claim register](../reference/claims.md). kind
evidence is never cited as an observed AKS deployment.
