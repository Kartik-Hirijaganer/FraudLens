# ADR-020 — vLLM and 4-bit AWQ for self-hosted SAR inference

- **Status:** Accepted; superseded on quality 2026-09-18 by
  [ADR-030](ADR-030-quality-gated-sar-model-cascade.md)
  (see [Amendment](#amendment--2026-09-18-superseded-on-quality-by-adr-030))
- **Date:** 2026-09-14

## Context

FraudLens needs a reproducible answer to whether a self-hosted, quantized instruction model can
draft grounded synthetic SAR narratives within a single-GPU memory and cost envelope. Model-weight
compression alone is insufficient: the comparison must preserve prompt, model family, GPU, vLLM
image, workload, and quality checks while measuring latency, throughput, memory, and cost.

The application already routes LLM work through a governed OpenAI-compatible client. The benchmark
must exercise that contract without making self-hosting the production default or weakening the
synthetic-only model-egress boundary.

### Options considered and rejected

1. **Compare unrelated BF16 and quantized models** — rejected because model-family differences
   would confound the quantization result.
2. **Use different hosts or images per arm** — rejected because hardware/runtime variance would
   invalidate the direct comparison.
3. **Measure only weight-file size** — rejected because serving memory, KV capacity, useful
   throughput, quality, and cost determine operational value.
4. **Make the benchmark endpoint the deployment default** — rejected because a temporary research
   host is not an availability, compliance, or operations commitment.
5. **Use an AMD GPU** — rejected because the frozen AWQ-Marlin vLLM path requires supported NVIDIA
   CUDA kernels.

## Decision

Use vLLM's OpenAI-compatible server to compare Qwen2.5-7B-Instruct BF16 with its 4-bit AWQ-Marlin
variant. Execute both arms sequentially on the same NVIDIA host and pinned image. RunPod Secure
Cloud RTX 4090 is the default Phase 11 provider; Azure A100/A10 is only an alternative when fresh
quota, capacity, and pilot-projected cost pass the STOP 3 gate.

The frozen full profile contains 1,000 measured synthetic investigation cases at concurrency 1, 8,
and 32, plus 40 development cases, 10 warm-ups per level, and 20 abstention fixtures. Capture parsed
model-weight memory, safetensors size, GPU/KV telemetry, TTFT, end-to-end latency, generated-token
and useful-draft throughput, cost, schema validity, citation grounding, fact coverage, abstention,
and unsupported claims. A mechanical report headline may claim only what those measurements prove.

Run two comparison modes: equal KV-cache utilisation for the primary latency/throughput comparison,
and maximum safe concurrency as a capacity observation. Persist prompts/outputs only in the local,
encrypted experiment workspace; publish aggregate, PHI-free, hash-bound evidence. A 100-case
application pass must prove that the normal FastAPI path can use the selected vLLM endpoint.

### Evidence

- [`config/vllm-bench.yaml`](../../../config/vllm-bench.yaml) freezes the cases, models, image,
  concurrency levels, quality thresholds, and telemetry contract.
- [`vllm-benchmark.md`](../../runbooks/vllm-benchmark.md) defines provider selection, admission,
  execution, export, and teardown.
- The measured [`vllm-awq-sar-benchmark`](../../reference/benchmarks/vllm-awq-sar-benchmark.md)
  and its bound frontend projection publish the 1,000-case, three-concurrency result. AWQ reduced
  model-weight memory by 63.5% and increased throughput by 60.8% at concurrency 32, while failing
  the reference-validity acceptance threshold; no quality-parity claim is permitted.

## Tradeoffs accepted

- One RTX 4090 is cost-efficient but lacks datacenter-ECC and does not predict multi-GPU behavior.
- Sequential arms reduce cost and host variance but cannot prove simultaneous capacity.
- Equal KV-cache utilisation improves fairness but differs from each arm's maximum capacity;
  reporting both modes keeps that distinction explicit.
- Self-hosting exposes operational work hidden by hosted APIs: image/model pinning, warm-up,
  telemetry, watchdogs, key delivery, checkpoint export, and verified teardown.

## Reconsider when

- **Met (2026-09-18).** Release 0.5.0 measured AWQ's grounding failure rate and replaced the raw
  route with the quality-gated cascade in [ADR-030](ADR-030-quality-gated-sar-model-cascade.md).
- A different model family, quantization format, vLLM major version, or GPU architecture is selected.
- Production data or real PHI is proposed for model egress.
- A managed endpoint meets the same evidence, privacy, and cost requirements with less operational
  burden.
- The application needs multi-GPU serving, HA, continuous capacity, or a formal inference SLO.

The published performance claim is limited to the measured efficiency result. The failed quality
criterion remains visible in the report and prevents treating AWQ as an unconditional runtime
default; any later gated cascade is separate release work.

## Amendment — 2026-09-18 (superseded on quality by ADR-030)

[ADR-030](ADR-030-quality-gated-sar-model-cascade.md) supersedes this record **on quality only**.
The memory, latency, and throughput findings below stand, and were re-measured in release 0.5.0.
What this record did not measure was grounding: raw AWQ fabricated citations on **85 of 1,000**
cases at concurrency 32 where BF16 fabricated none, so AWQ is no longer served ungated.
