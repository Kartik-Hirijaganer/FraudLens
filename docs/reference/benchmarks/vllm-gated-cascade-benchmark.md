# Gated SAR cascade benchmark

**Gated awq-bf16-unconstrained served 99.7% of 1000 cases with 9.2% escalated; case p95 latency 6.3% higher than bf16-baseline at concurrency 32 across two endpoints, not one; AWQ model weights 63.5% smaller.**

- Run: `vllm-bench-be12675628805a53` (vllm-sar-bench-v5, vllm-cascade-report-v1)
- Commit: `615285e0e63f85eb2ee0661ae21bd36a4cbf8f8d`
- Config SHA-256: `57df143fde48dd7cfee6c14d366ea261d8bfa36c949e1be5ad5cbf0d7d70f169`
- Cases SHA-256: `2e1df4db3099fcd411158fd18d1a6bcb9cf69bf1dadef8138d68712a049376e2`
- Prompt: `v5@5.0.0` (`7041a9a4f2b5f985cf9ae7861b671d11432bd22c218a7bbb39094d1451cef77d`)
- Quality policy SHA-256: `c9100eb3bfc30a06914b381c4c337c3dca9d546a18e7cd7164f0e26253bb716a`
- Measured cases: 1000 from `ibm-final-test`
- Window: 2026-09-18T00:00:29.434193+00:00 → 2026-09-18T03:14:17.855360+00:00
- Baseline scenario: `bf16-baseline`
- Acceptance met: yes

## Endpoint provenance


| Role | Model | Revision | Image digest | GPU | Driver | CUDA | Weights GiB | KV tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| awq | Qwen/Qwen2.5-7B-Instruct-AWQ | b25037543e93 | sha256:df2607b26bdd | NVIDIA GeForce RTX 4090 | 580.159.04 | not captured | 5.2036 | 279,040 |
| bf16 | Qwen/Qwen2.5-7B-Instruct | a09a35458c70 | sha256:df2607b26bdd | NVIDIA GeForce RTX 4090 | 580.178.04 | not captured | 14.2488 | 109,664 |

External hosted routes: none exercised by this run.

## Scenario performance (model calls)

| Scenario | Stages | Concurrency | p50 ms | p95 ms | req/s | useful drafts/s | Error rate | Cost / 1k |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bf16-baseline | bf16 | 1 | 5413.0 | 6365.0 | 0.185 | 0.169 | 0.0000 | $1.1134 |
| bf16-baseline | bf16 | 8 | 7057.0 | 8379.4 | 1.134 | 1.038 | 0.0000 | $0.1813 |
| bf16-baseline | bf16 | 32 | 13225.0 | 16163.9 | 2.399 | 2.190 | 0.0000 | $0.0857 |
| awq-raw | awq | 1 | 2797.0 | 3213.6 | 0.354 | 0.295 | 0.0000 | $0.5807 |
| awq-raw | awq | 8 | 4492.0 | 6741.3 | 1.650 | 1.388 | 0.0000 | $0.1246 |
| awq-raw | awq | 32 | 12608.5 | 16228.2 | 2.459 | 2.066 | 0.0000 | $0.0836 |
| awq-bf16-unconstrained | awq→bf16 | 32 | 10649.0 | 14780.2 | 2.965 | 2.521 | 0.0000 | $0.1510 |

## Cascade behaviour (cases)

| Scenario | Concurrency | Cases | Escalated | Served | Terminal fail | Case p50 ms | Case p95 ms | GPU s / case | Aggregate peak MiB | Endpoints |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| awq-bf16-unconstrained | 32 | 1000 | 9.2% | 99.7% | 0.3% | 11008.5 | 17184.2 | 11.510 | 43540 | 2 |

## Stage accounting

| Scenario | Stage | Share served | Pass rate reached | Total s | Prompt tokens | Output tokens | Retries | Serving errors | Provider cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| awq-bf16-unconstrained | awq | 90.5% | 90.8% | 10996.7 | 1,912,237 | 314,647 | 0 | 0 | $0.000000 |
| awq-bf16-unconstrained | bf16 | 9.2% | 100.0% | 513.4 | 172,638 | 25,175 | 0 | 0 | $0.000000 |

## Terminal failures and why

| Scenario | Concurrency | Terminal fail | Reason codes |
| --- | --- | --- | --- |
| bf16-baseline | 1 | 0.6% | `no_citations` x 3, `unmapped_narrative_fact` x 3 |
| bf16-baseline | 8 | 0.7% | `no_citations` x 3, `unmapped_narrative_fact` x 4 |
| bf16-baseline | 32 | 0.5% | `no_citations` x 3, `unmapped_narrative_fact` x 2 |
| awq-raw | 1 | 10.2% | `citation_fabricated` x 96, `no_citations` x 3, `unmapped_narrative_fact` x 3 |
| awq-raw | 8 | 9.4% | `citation_fabricated` x 87, `no_citations` x 3, `unmapped_narrative_fact` x 4 |
| awq-raw | 32 | 9.1% | `citation_fabricated` x 85, `no_citations` x 3, `unmapped_narrative_fact` x 4 |
| awq-bf16-unconstrained | 32 | 0.3% | none recorded (terminal failures were refused before any model call) |

## Comparisons

| Metric | Concurrency | Baseline | Baseline value | Scenario | Observed | Change | Compares |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cascadeLatencyP95Ms | 1 | bf16-baseline | 6365 | awq-raw | 3213.65 | -49.5% | same hardware |
| finalPassRate | 1 | bf16-baseline | 0.994 | awq-raw | 0.898 | -9.7% | same hardware |
| gpuHoursPerCase | 1 | bf16-baseline | 0.00150457 | awq-raw | 0.000784686 | -47.8% | same hardware |
| cascadeLatencyP95Ms | 8 | bf16-baseline | 8379.45 | awq-raw | 6741.3 | -19.5% | same hardware |
| finalPassRate | 8 | bf16-baseline | 0.993 | awq-raw | 0.906 | -8.8% | same hardware |
| gpuHoursPerCase | 8 | bf16-baseline | 0.000244956 | awq-raw | 0.000168326 | -31.3% | same hardware |
| cascadeLatencyP95Ms | 32 | bf16-baseline | 16163.9 | awq-raw | 16228.2 | +0.4% | same hardware |
| finalPassRate | 32 | bf16-baseline | 0.995 | awq-raw | 0.909 | -8.6% | same hardware |
| gpuHoursPerCase | 32 | bf16-baseline | 0.000115806 | awq-raw | 0.000112945 | -2.5% | same hardware |
| cascadeLatencyP95Ms | 32 | bf16-baseline | 16163.9 | awq-bf16-unconstrained | 17184.2 | +6.3% | two endpoints vs one |
| finalPassRate | 32 | bf16-baseline | 0.995 | awq-bf16-unconstrained | 0.997 | +0.2% | two endpoints vs one |
| gpuHoursPerCase | 32 | bf16-baseline | 0.000115806 | awq-bf16-unconstrained | 0.000204032 | +76.2% | two endpoints vs one |

## Accepted-draft quality

| Scenario | Accepted | Schema valid | Reference validity | Citation recall | Fact coverage | Fabricated refs | Unsupported claims |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bf16-baseline | 994 | 1.0000 | 1.0000 | 0.9188 | 0.9804 | 0 | 0 |
| awq-raw | 898 | 1.0000 | 1.0000 | 0.8963 | 0.9822 | 0 | 0 |
| awq-bf16-unconstrained | 997 | 1.0000 | 1.0000 | 0.9022 | 0.9827 | 0 | 0 |

## Acceptance

| Criterion | Result | Observed | Required |
| --- | --- | --- | --- |
| scenario_matrix_measured | PASS | bf16-baseline@1/8/32, awq-raw@1/8/32, awq-bf16-unconstrained@32 | at least one multi-stage scenario measured at a declared level |
| gate_verdict_parity | PASS | 0 attempts disagree | 0 attempts disagree |
| zero_serving_errors | PASS | 0 serving errors | 0 serving errors |
| cascade_final_pass_rate | PASS | 0.9970 | >= 0.99 |
| accepted_reference_validity | PASS | 1.0000 | >= 1.0 |
| token_accounting_drift | PASS | 0.0000 | <= 0.05 |
| gpu_telemetry_per_role | PASS | sampled ['awq', 'bf16'] | sampled ['awq', 'bf16'] |
| weight_memory_reduction | PASS | 0.6348 | >= 0.5 |
| single_quality_policy | PASS | c9100eb3bfc30a06914b381c4c337c3dca9d546a18e7cd7164f0e26253bb716a | exactly one recorded policy hash |

## Disclosures

- Cascade case latency is measured across TWO simultaneously provisioned endpoints; the BF16 and AWQ raw comparisons use ONE. Every comparison row states which it is, and p95 is published next to GPU-seconds per case and aggregate peak device memory for that reason.
- Guided (constrained) decoding was excluded from the published matrix after its live latency canary: the exact fact-object grammar passed quality at 8/8 but measured a 441.6-second p95 at concurrency 32. The shipped contract is a compact prose envelope the backend hydrates, judged by the identical deterministic gate.
- Three of 1,000 cases carry no evidence and fail preflight before any model call. They are counted as terminal cascade failures, never omitted, and are the entire gap between the 99.7% served share and 100%.
- The RunPod provider stopped reporting Pod volume encryption during this release, so the frozen contract's encrypted-volume verification could not be satisfied. The corpus is the public IBM AML synthetic dataset and contains no PHI; the observed encryption state is recorded on every affected ledger row (ADR-030).
- CUDA runtime capture was added after this matrix ran, so the endpoint provenance publishes it as not captured rather than inferring it from the driver version. The pinned image digest fixes the CUDA toolchain the run actually used.
- Accepted-draft quality is measured over served drafts only, so abstention correctness is published as null rather than as a rate: computed over accepted drafts it would be a vacuous 1.0. Abstention appears instead as the evidence-free cases this run refused before any model call, in the terminal-failure table.
- No hosted external tier was exercised: the published cascade is AWQ then BF16, both self-hosted. The external tier ships configured but unmeasured, so no OpenRouter zero-data-retention eligibility snapshot exists for this run.
