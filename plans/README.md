# FraudLens Implementation Plans

Non-trivial work starts with a dated, phase-based plan so implementation and review share one
verifiable contract. The canonical governance is [AGENTS.md](../AGENTS.md).

## Naming and structure

Use `plans/YYYY-MM-DD-<short-kebab-title>.md`. Each independently auditable unit uses a
`## Phase N — <name>` heading and states What, Why, How, Files, Tests, and Acceptance criteria.
After implementation, run the read-only audit for the relevant phase:

```text
drift-check plans/<file>.md phase=<N>
drift-check plans/<file>.md all
```

Plans describe intent; implemented code, generated contracts, automated tests, and published
evidence remain the proof. Commit and push restrictions in AGENTS.md apply to every phase.

## Active plans

| Plan | Scope | Status |
| --- | --- | --- |
| [2026-09-16 SAR quality gate, cascade, and gated benchmark](2026-09-16-sar-quality-gate-cascade-and-gated-benchmark.md) | Release 0.5.0 deterministic `SARQualityGate`, AWQ→BF16→external cascade, and production-path re-benchmark | Active — Phases 1–5 implemented; awaiting the human-authorized `v0.5.0` tag |

## Release 0.4.0 scope

Items 1-5 were scoped out of the 0.3 closeout; the 0.5.0 plan above delivers them. Item 6 shipped
with the Azure deployment work under the now-retired 0.4.0 plan.

1. ~~Add a deterministic `SARQualityGate` over ChromaDB-retrieved source spans.~~ — **done.** The
   gate runs on the production path before anything is persisted or streamed
   ([ADR-030](../docs/architecture/adr/ADR-030-quality-gated-sar-model-cascade.md)).
2. ~~Route AWQ first, then regenerate with BF16 or GPT-5 mini when the gate fails.~~ — **done for
   AWQ→BF16**, measured at 9.2% escalation with every escalated case served
   ([gated-cascade report](../docs/reference/benchmarks/vllm-gated-cascade-benchmark.md)). The
   hosted GPT-5-mini tier ships configured and **unmeasured**; its pilot was not run.
3. ~~Stream the model, validation, rejection reason, and escalation decision over the existing SSE
   channel and surface the trace in the frontend.~~ — **done.** Stage frames carry the decision and
   its reason codes and never the rejected narrative, including on reconnect.
4. ~~Publish a dedicated AWQ evaluation note that explains the quality gap and the
   FrugalGPT/model-cascade pattern.~~ — **done.** The quality gap is measured, not asserted: raw
   AWQ fabricated citations on 85 of 1,000 cases at concurrency 32 and BF16 on none.
5. ~~Ground the validator in FinCEN SAR narrative field requirements.~~ — **done** via the closed
   evidence catalog and asserted-fact matching.
6. ~~Run the human-approved AKS demonstration~~ — **done 2026-09-16.** Session
   `aks-demo-20260915-01` applied the validated Terraform, deployed, captured
   [measured HPA and durability evidence](../docs/reference/benchmarks/aks-hpa-scaling.md), then
   destroyed the cluster and verified it clean.

The supported AKS wording is no longer the kind-only phrasing: it is set by
[ADR-021's amendment](../docs/architecture/adr/ADR-021-aks-ephemeral-kubernetes-demonstration.md),
and every present-tense claim is capped by the
[claim register](../docs/reference/claims.md). kind evidence is still never cited as an observed AKS
deployment.

## Retired plans

Retired plan files are intentionally absent from the working tree. Standalone ADRs, runbooks, and
tests are the maintained evidence; commit hashes identify the history transition without creating
dead Markdown links.

| Retired plan | Removing commit | Maintained replacement |
| --- | --- | --- |
| `2026-06-09-tech-stack-foundation-and-workflows.md` | `4cd0e63` | [Makefile](../Makefile), [architecture](../docs/architecture/ARCHITECTURE.md), and [ADR index](../docs/architecture/adr/README.md) |
| `2026-06-12-aml-fraud-detection-system.md` | `ef51c0c` | [Architecture](../docs/architecture/ARCHITECTURE.md), [reference docs](../docs/reference), and ADR-001 through ADR-016 in the [ADR index](../docs/architecture/adr/README.md) |
| `2026-07-14-gfp-isolation-gap-visual-study.md` | `ef51c0c` | [ADR-017](../docs/architecture/adr/ADR-017-graph-feature-serving-boundary.md) and [published study](../docs/reference/benchmarks/gfp-tenant-isolation-study.md) |
| `2026-07-25-portfolio-demo-story.md` | Not present in tracked history; dead reference removed in Phase 0 | [ADR-018](../docs/architecture/adr/ADR-018-portfolio-demo-data-provenance.md) and [portfolio demo runbook](../docs/runbooks/portfolio-demo.md) |
| `2026-08-17-multi-agent-investigation-and-azure-deployment.md` | `9fac245` | [ADR-019](../docs/architecture/adr/ADR-019-multi-agent-sar-drafting.md) |
| `2026-09-13-local-pr-check-command.md` | `9fac245` | `make pr-check` in the [Makefile](../Makefile) and [scripts/check_pr_title.sh](../scripts/check_pr_title.sh) |
| `2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` | `9fac245` | [ADR-020](../docs/architecture/adr/ADR-020-vllm-awq-self-hosted-sar-inference.md) through [ADR-028](../docs/architecture/adr/ADR-028-paid-experiment-governance.md) in the [ADR index](../docs/architecture/adr/README.md), plus the published [vLLM/AWQ benchmark](../docs/reference/benchmarks/vllm-awq-sar-benchmark.md), [full-data training](../docs/reference/benchmarks/ibm-full-data-training.md), and [HPA scaling](../docs/reference/benchmarks/k8s-hpa-scaling.md) reports |
| `2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md` | `TBD` | [ADR-021's amendment](../docs/architecture/adr/ADR-021-aks-ephemeral-kubernetes-demonstration.md) and [ADR-029](../docs/architecture/adr/ADR-029-recurring-operational-budget.md) in the [ADR index](../docs/architecture/adr/README.md), plus the [Azure deploy runbook](../docs/runbooks/azure-deploy.md), the [cost model](../docs/reference/cost-model.md), and the published [AKS HPA scaling report](../docs/reference/benchmarks/aks-hpa-scaling.md) |

## Documentation backlog

- Replace historical `plan §N` prose references in source docstrings and Makefile comments with
  stable ADR or documentation links. Phase 0 fixes dead links; the broader wording cleanup is
  deferred to the release documentation phase because it does not change runtime behavior.
