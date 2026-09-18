# FraudLens 10-minute technical interview guide

Use this script to demonstrate the system without overstating paid or cloud evidence. Run the local
demo before the conversation; keep the published evidence pages open. Every application record and
benchmark case is synthetic or public synthetically generated data.

## 0:00–1:00 — Frame the problem and trust boundary

Open the dashboard and explain: FraudLens turns a transaction into an explainable AML investigation,
not an autonomous filing decision. The browser is untrusted; JWT identity establishes `agency_id` at
the FastAPI edge; every repository/background operation remains tenant-scoped; a human approves or
rejects a SAR.

Principal trade-off: a deterministic scoring/control plane surrounds the probabilistic LLM edge.
That gives degraded-but-reviewable behavior when RAG or drafting fails instead of losing the score.

## 1:00–3:00 — Trace one investigation

Open a high-risk transaction, start an investigation, and show the status progression. Point out the
rule hits, calibrated probability/risk band, and signed SHAP contributions. Open the resulting alert
and show citations plus “What the model saw.”

Explain that the disclosure is reconstructed by the backend from persisted tenant-scoped evidence
and passed through the same fail-closed model-egress projection. A drafting failure becomes
`drafting-blocked`; risk evidence remains visible, but no narrative is presented as complete.

## 3:00–4:30 — Durable execution

Show the investigation worker and [kind evidence](benchmarks/k8s-hpa-scaling.md). The API persists a
pending run; workers claim it with a lease, heartbeat, and fencing token. After process/pod loss, a
replacement may repeat a bounded attempt, while stale attempts cannot commit the terminal result.

Why leases instead of “exactly once”: exactly-once execution across process/network failure is not a
credible primitive here. FraudLens provides at-least-once work with idempotent persistence and one
accepted fenced terminal write—an explicit, testable contract.

## 4:30–6:00 — Kubernetes and Azure boundary

Show the 1 → 5 → 1 HPA trace and 100/100 durable completion after worker deletion. Then show the AKS
Terraform/runbook. Release 0.3 measured behavior on kind and validated the AKS shape without apply.

Why AKS is ephemeral: Container Apps remains the application deployment target. AKS demonstrates
portable manifests, Cilium policy, workload identity, HPA, and failure recovery without paying for a
permanent personal-project cluster. The precise claim is “deployable to AKS; proven on Kubernetes
using kind,” until a separately approved AKS session publishes its own evidence.

## 6:00–7:30 — Full-data temporal training

Open the training benchmark page. Explain the 68,228,066 frozen IBM source rows versus usable,
training, calibration, and untouched holdout counts. Accounts are assigned as whole chronological
cohorts to prevent future leakage; feature parity compares offline windows with the live builder.

Why calibration-derived thresholds: rare-event calibrated probabilities do not naturally align with
fixed risk-band cutoffs. Calibration-fold quantiles encode review capacity; holdout measures the
frozen choice. The active pointer still moves only through quantitative gates plus human promotion.

Disclose the current state: all 68,228,066 source rows were processed and the aggregate report is
published; only the pre-registered HI-Medium candidate passed its promotion gates.

## 7:30–9:00 — vLLM BF16 versus AWQ study

Open the inference benchmark page. Explain the frozen 1,000-case workload, three concurrency levels,
same host/image/model-family comparison, quality gates, telemetry, cost, and hash-bound publication.

Why AWQ: 4-bit weight-only quantization reduced parsed model-weight memory by 63.5% and improved
throughput by 60.8% at concurrency 32 on the measured RTX 4090 run. The same report records the
reference-validity failure and quality regressions, so the result is an efficiency demonstration,
not an unconditional recommendation or quality-parity claim.

Have the honest number ready: at concurrency 1 AWQ is 49.5% faster than BF16, at 8 it is 19.5%
faster, and at 32 it is 0.4% **slower**. The memory reduction holds at every level; the latency win
does not. Say that before you are asked.

Why equal utilisation: matching KV-cache utilisation controls one major memory/capacity confound for
the primary comparison. Maximum safe concurrency is reported separately because each arm may have a
different capacity ceiling. The temporary RunPod Pod and encrypted volume were deleted after the
benchmark and the provider cleanup query returned no matching resources.

## 9:00–10:00 — The quality-gated cascade, governance, and close

Lead with the finding, not the architecture. Quantization did not cost fluency — it cost grounding:
over the same 1,000 cases at concurrency 32, raw AWQ fabricated citations on **85** and BF16 on
**none**. Before release 0.5.0 the production path hid exactly that, because `parse_and_ground`
silently deleted fabricated ids and returned a draft that looked clean.

Open the [gated-cascade report](benchmarks/vllm-gated-cascade-benchmark.md) and walk three rows: the
cascade served 99.7% versus 99.5% for BF16 alone and 90.9% for AWQ alone; it escalated 9.2% of cases
and BF16 resolved every one of them; it cost 6.3% more p95 and 76% more GPU-time **across two
endpoints, not one** — point at the `Compares` column, because that distinction is the first thing a
good interviewer will probe.

Then the part worth the most: `gate_verdict_parity`. The report refuses to publish unless the gate
verdict the production drafter recorded at request time equals the verdict re-derived from the
persisted output on all 1,092 attempts. That is what makes the benchmark evidence about the shipped
path rather than a second implementation agreeing with itself — and it is why the old model-only
measurement runner could be deleted.

Have the rejections ready too: constrained decoding would make fabrication structurally impossible
and was measured at a 441.6-second p95, so it ships configured and unshipped. The hosted third tier
ships configured and unmeasured, so the report claims nothing about it.

## Governance and close

Show the claim register and root Makefile. Summarize the provider-free citation/hallucination/egress
gates, 90% coverage, source-file cap, deterministic docs, dependency/IaC scans, and the experiment
ledger. Paid creation, uploads, destroy, deploys, commits, pushes, tags, and releases are human-gated.

Close with the architecture judgment: FraudLens separates product truth from research truth. Runtime
claims come from tenant-safe application tests; benchmark claims come only from published measured
artifacts; validate-only cloud infrastructure is labeled as such.

## Fast evidence index

| Topic | Evidence |
| --- | --- |
| Architecture | [`ARCHITECTURE.md`](../architecture/ARCHITECTURE.md) |
| Claims | [`claims.md`](claims.md) |
| Kubernetes | [`k8s-hpa-scaling.md`](benchmarks/k8s-hpa-scaling.md) |
| Durable execution | [`ADR-027`](../architecture/adr/ADR-027-durable-investigation-execution.md) |
| Model egress | [`ADR-026`](../architecture/adr/ADR-026-synthetic-only-model-egress.md) |
| Full-data method | [`data-batch.md`](../runbooks/data-batch.md) |
| Inference protocol | [`vllm-benchmark.md`](../runbooks/vllm-benchmark.md) |
| Paid governance | [`ADR-028`](../architecture/adr/ADR-028-paid-experiment-governance.md) |
| Quality-gated cascade | [`ADR-030`](../architecture/adr/ADR-030-quality-gated-sar-model-cascade.md) and [`vllm-gated-cascade-benchmark.md`](benchmarks/vllm-gated-cascade-benchmark.md) |
