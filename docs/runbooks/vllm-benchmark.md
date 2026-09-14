# vLLM BF16-versus-AWQ benchmark

The frozen benchmark compares Qwen2.5-7B-Instruct BF16 with its 4-bit AWQ-Marlin variant on the
same GPU and vLLM image. RunPod Secure Cloud RTX 4090 is the default Phase 11 host; Azure A100/A10 is
only an opportunistic alternative if fresh quota and budget admission pass at STOP 3.

## Provider-free validation

```bash
make vllm-bench-test
make vllm-bench-validate
make runpod-gpu-test
PROFILE=full SOURCE=ibm-final-test make vllm-bench-cases
```

The full corpus must contain 1,000 measured cases (500 HI-Small, 250 HI-Medium, 250 LI-Medium), 40
development cases, 10 warm-ups per concurrency level, and 20 abstention fixtures. Case generation
fails closed until the Phase 6 application-candidate artifacts exist. Never substitute smoke cases
for release evidence.

## STOP 3 provider decision

Refresh Azure A100/A10 and regional Spot quotas, current Azure/RunPod prices, and the projected
runtime with the required 30% margin. Select Azure only when quota and the $25 allocation both fit;
otherwise choose RunPod immediately. Record hardware, purchase option, quote date, risks, and exact
commands in the decision report. Creation still requires a separate explicit approval.

For the default path, prepare a restricted `RUNPOD_API_KEY` in Infisical `prod` `/ml`, disable
automatic top-ups, configure a dedicated SSH key, and keep the random `VLLM_API_KEY` out of source.
The pod exposes SSH only, uses an encrypted network volume, pins the vLLM image digest, and has an
eight-hour stop watchdog.

Install the official CLI on macOS with `brew install runpod/runpodctl/runpodctl`, then authenticate
the Infisical CLI. Run every operator command through the `/ml` secret-injection boundary:

```bash
export RUN=vllm-bench-<16-lowercase-hex>
infisical run --env=prod --path=/ml -- make RUN="$RUN" runpod-gpu-plan
infisical run --env=prod --path=/ml -- make CONFIRM=yes RUN="$RUN" runpod-gpu-up
infisical run --env=prod --path=/ml -- make \
  CONFIRM=yes RUN="$RUN" \
  VLLM_CASES=.local/vllm-bench/cases-ibm-final-test-full.json runpod-gpu-sync
```

## Smoke, development, and full-run gates

On the approved host, start vLLM as a direct process; nested Docker is not required. The operator
captures the image digest, GPU/driver/CUDA facts, vLLM version, startup logs, KV capacity, and
`nvidia-smi` telemetry.

1. Start AWQ and run the smoke profile; verify logs, SSE token accounting, telemetry, checkpoints,
   and report schema.
2. Stop AWQ, run the 40-case development set on BF16 and AWQ, and project full cost from measured
   runtime.
3. Stop and request pilot-to-full approval.
4. Run all 1,000 cases at concurrency 1, 8, and 32 on both arms without changing host or protocol.
5. Run the 100-case application pass, build, validate, and publish the report.

The per-arm command surface is:

```bash
ARM=awq make vllm-bench-serve
RUN=$RUN ARM=awq PROFILE=smoke SOURCE=sar-eval HOST=runpod-rtx4090 make vllm-bench-run
make vllm-bench-stop
RUN=$RUN VLLM_CASES=.local/vllm-bench/cases-ibm-final-test-full.json make vllm-bench-report
RUN=$RUN make vllm-bench-publish
```

For the functional pass, keep the AWQ process running behind the SSH tunnel. Register the
gates-passed HI-Medium bundle in the chosen development database first; production promotion is a
separate lifecycle operation and is never implied here. Then run the API and worker in separate
terminals with the same Infisical injection:

```bash
infisical run --env=prod --path=/ --recursive -- env \
  FRAUDLENS_MODEL_ARTIFACTS_DIR=.local/fulldata/artifacts \
  uv run python scripts/activate_model.py --label xgb-ibm-aml-hi-medium-5835992a6919
make run-live-vllm
make run-live-vllm-worker
```

From a third terminal, submit exactly 100 calibrated synthetic cases at concurrency four. The
runner requires `/readyz` to report `llmProvider=vllm`, requires every snapshot to show a durable
worker claim, validates the persisted SAR schema and citation membership, and writes only PHI-free
functional evidence. It intentionally does not publish request timings as benchmark latency.

```bash
RUN=vllm-e2e-<16-lowercase-hex> \
MODEL_OVERRIDE=xgb-ibm-aml-hi-medium-5835992a6919 \
make vllm-bench-e2e
```

Do not claim an AWQ latency or throughput improvement unless the observations prove it. A valid
result may show only the required model-weight memory reduction; disclose neutral or adverse
performance and every unmet warning.

## Export and teardown

Export before deletion and verify artifact lineage locally:

```bash
RUN=$RUN make runpod-gpu-export
CONFIRM=yes RUN=$RUN make runpod-gpu-stop
```

Stop for teardown permission. Deleting the pod and its volume is irreversible:

```bash
CONFIRM=yes RUN=$RUN make runpod-gpu-down
RUN=$RUN make runpod-gpu-verify-clean
uv run python scripts/experiment_budget.py ledger-check
```

Stopped pods may retain billable storage. Teardown is complete only when the provider query finds
neither the identity-matched pod nor its network volume and the ledger records the settled session.
