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
- Require explicit human permission before every `gpu-bench-up`, cloud mutation, release-asset
  upload, and teardown action covered by Golden Rule 7.
- Resume from per-arm/per-concurrency checkpoints; never discard completed measurements to improve
  a result.

## Steps

1. Run free prerequisites: `make vllm-bench-test`, `make vllm-bench-validate`, case generation for
   the selected profile, and `make gpu-bench-plan`.
2. Confirm quota, price provenance, allocation, watchdog, operator CIDR, and an open ledger row.
3. After explicit permission, create the GPU host with the required `CONFIRM=yes` gate.
4. Run the AWQ smoke profile first and inspect request completion, telemetry, checkpoint, and report
   shape before expanding the workload.
5. Run the development set on both arms, project full duration and cost, and use
   `scripts/experiment_budget.py admit --allocation azure_gpu_benchmark`; pause for the human
   pilot-to-full decision.
6. Execute BF16 then AWQ on the host, stopping one server before starting the other. Reuse the same
   run id and resume checkpoints after a spot eviction.
7. Build the report, run `make vllm-bench-validate`, review it, then publish only the validated,
   hash-bound pair. Treat the application pass as functional evidence, not a latency measurement.
8. Export artifacts, reconcile the ledger, obtain teardown permission, run `make gpu-bench-down`,
   and finish with `make gpu-bench-verify-clean`.

## Verification

Evidence is complete only when all of these hold:

- Provenance records run id, config/case hashes, model revisions, image digest, vLLM/GPU/driver,
  host, region, purchase option, timestamps, and price source/date.
- Both arms contain the same successful case matrix, warm-up exclusions, concurrency levels, and
  metric definitions; quality deltas are reported alongside performance.
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
