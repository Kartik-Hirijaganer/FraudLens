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
| [2026-08-17 multi-agent investigation and Azure deployment](2026-08-17-multi-agent-investigation-and-azure-deployment.md) | Bounded multi-agent SAR drafting and Azure delivery | Implemented; retained for audit history |
| [2026-09-13 local PR check command](2026-09-13-local-pr-check-command.md) | One-command local PR preflight | Active |
| [2026-09-13 vLLM/AWQ, full-data training, and AKS](2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md) | Release 0.3.0 benchmark, data, durability, and Kubernetes work | Implemented; release evidence published |

## Next release: 0.4.0 first steps

Release 0.4 work is explicitly outside the 0.3 closeout:

1. Add a deterministic `SARQualityGate` over ChromaDB-retrieved source spans.
2. Route AWQ first, then regenerate with BF16 or GPT-5 mini when the gate fails.
3. Stream the model, validation, rejection reason, and escalation decision over the existing SSE
   channel and surface the trace in the frontend.
4. Publish a dedicated AWQ evaluation note that explains the quality gap and the
   FrugalGPT/model-cascade pattern.
5. Ground the validator in FinCEN SAR narrative field requirements.
6. Run the human-approved AKS demonstration: apply the validated Terraform, deploy, capture HPA and
   durability evidence, then stop or destroy and verify clean.

Until AKS evidence exists, the supported wording is “deployable to Azure AKS; autoscaling and
durability proven on Kubernetes using kind.”

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

## Documentation backlog

- Replace historical `plan §N` prose references in source docstrings and Makefile comments with
  stable ADR or documentation links. Phase 0 fixes dead links; the broader wording cleanup is
  deferred to the release documentation phase because it does not change runtime behavior.
