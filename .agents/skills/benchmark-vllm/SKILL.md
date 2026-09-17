---
name: benchmark-vllm
description: Operate the FraudLens vLLM BF16-versus-AWQ benchmark protocol with smoke-first execution, resumable checkpoints, spend gates, evidence validation, and verified teardown.
---

# Benchmark vLLM

Drive the governed SAR-inference benchmark without weakening its fairness, evidence, or spend
controls.

## When To Use

Use for `make vllm-bench-*` work, GPU benchmark planning or execution, BF16/AWQ comparison,
benchmark publication, or recovery after a spot interruption.

## Rules

- Use synthetic, hash-bound cases only. Never send real PHI or production case content.
- Run the smoke profile before any development or full matrix.
- Keep arms comparable: identical cases, concurrency, output limits, retry policy, server flags other
  than quantization, and warm-up treatment.
- Require explicit human permission before every `runpod-gpu-up`, cloud mutation, release-asset
  upload, and teardown action covered by Golden Rule 7.
- Resume from per-arm/per-concurrency checkpoints; never discard completed measurements to improve
  a result.
- Run the free replay pilot (`make vllm-bench-cascade-pilot RUN=...`) before any paid cascade run:
  it is the admission projection, and it sizes and costs the live matrix.
- Cascade scenarios measure the production `QualityGatedSarDrafter` over a named SAR profile, never
  a benchmark-only client or quality wrapper.
- Cascade scenarios need both endpoint roles reachable at once; raw arm comparisons keep one active
  endpoint so the quantization comparison stays equal-hardware.
- Any temporary application budget raise is explicit, owner-approved, recorded in the ledger row,
  and restored afterwards; never bypass `BudgetGuard`.

## Steps

1. Run free prerequisites: `make vllm-bench-test`, `make vllm-bench-validate`,
   `make vllm-bench-cascade-pilot RUN=<prior run>`, case generation for the selected profile, and
   `make runpod-gpu-plan`.
2. Confirm quota, price provenance, allocation, watchdog, operator CIDR, and an open ledger row.
3. After explicit permission, create the GPU host with the required `CONFIRM=yes` gate.
4. Run the AWQ smoke profile first and inspect request completion, telemetry, checkpoint, and report
   shape before expanding the workload.
5. Run the development set on both arms, project full duration and cost, and use
   `scripts/experiment_budget.py admit --allocation gpu_benchmark`; pause for the human
   pilot-to-full decision.
6. Execute BF16 then AWQ on the host, stopping one server before starting the other. Reuse the same
   run id and resume checkpoints after a spot eviction. Cascade scenarios instead keep both endpoint
   roles running and report GPU-hours per case plus aggregate resident memory.
7. Build the report, run `make vllm-bench-validate`, review it, then publish only the validated,
   hash-bound pair. Treat the application pass as functional evidence, not a latency measurement.
8. Export artifacts, reconcile the ledger, obtain teardown permission, run `make runpod-gpu-down`,
   and finish with `make runpod-gpu-verify-clean`.

## Verification

Evidence is complete only when all of these hold:

- Provenance records run id, config/case hashes, model revisions, image digest, vLLM/GPU/driver,
  host, region, purchase option, timestamps, and price source/date.
- Both arms contain the same successful case matrix, warm-up exclusions, concurrency levels, and
  metric definitions; quality deltas are reported alongside performance.
- Cascade scenarios report stage pass, escalation, external, and final pass rates, stage mix, and
  GPU-hours per case, and the live escalation rate is compared against the committed replay pilot.
- Latency percentiles, TTFT, request/token throughput, useful throughput, GPU/KV/queue telemetry,
  memory, and cost are derived from captured data rather than authored prose.
- Projected and actual spend are reconciled to the allocation and ledger.
- Claims match validation outcomes, disclose adverse results, and distinguish synthetic benchmark
  evidence from production behavior.
- Teardown verification finds no orphan GPU host, disk, IP, NIC, resource group, or storage.

## Never Do

- Never run the full profile before smoke and budget admission.
- Never change the frozen protocol after seeing results or compare unequal arms without disclosure.
- Never publish before `vllm-bench-validate`, leak an API key, or claim benchmark latency from the
  tunneled end-to-end pass.
- Never leave a paid host running because artifact publication or a benchmark arm failed.
- Never start a paid cascade run without a committed replay pilot, and never present cascade
  throughput as a same-resource gain over a single-endpoint arm.
