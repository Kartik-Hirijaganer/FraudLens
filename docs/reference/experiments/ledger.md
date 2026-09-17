# Experiment Budget Ledger

This ledger is the auditable spend record for bounded FraudLens experiments. A paid run needs a
current-plan row before admission, an allocation from
[`config/experiments/budget.yaml`](../../../config/experiments/budget.yaml), and `yes` in
**Teardown verified** after its ephemeral resources stop. Historical rows exist only so every
published study run remains traceable; they do not consume the new plan's $75 ceiling.
When a resource session produces an aggregate with a distinct logical run ID, **Evidence run IDs**
binds that report to the session without recording or charging the same spend twice.

Validate the resource sessions and published-report coverage with:

```bash
uv run python scripts/experiment_budget.py ledger-check
```

## Day-1 owner actions

These actions are deliberately human-owned. `pending` is not an approval ID and must be replaced
with the Azure request ID or verification date after the owner completes each action. No agent may
invent an identifier or infer permission to mutate a cloud or secret account.

| Action | Region/path | Target | Requested/verified at | Request/evidence ID | Status |
| --- | --- | --- | --- | --- | --- |
| Azure quota | westus3 | Total regional standard vCPUs | 2026-09-14 read | — | current limit 72; verified |
| Azure quota | westus3 | Standard EADSv5 Family vCPUs | 2026-09-14 read | — | current limit 32; verified |
| Azure quota | westus3 | Total regional low-priority/Spot vCPUs | 2026-09-14 read | — | current limit 3; insufficient for E16ads v5 |
| Azure quota | westus3 | Standard NCADS_A100_v4 Family vCPUs = 24 | 2026-09-14 read | — | current limit 0; request pending |
| Azure quota | westus3 | Standard NVADSA10v5 Family vCPUs = 36 | 2026-09-14 read | — | current limit 0; request pending |
| Azure quota | westus3 | Standard NCads H100 v5 Family vCPUs = 40 | 2026-09-14 assessment | — | request was 24; insufficient even if approved; capacity pending |
| Azure quota | westus3 | Standard NVads V710 v5 Family | 2026-09-14 assessment | — | unsupported in region and AMD GPU is incompatible with frozen `awq_marlin` protocol; abandon |
| CPU purchase path | westus3 | Standard_E16ads_v5 pay-as-you-go | 2026-09-14 | — | selected; does not depend on Spot quota |
| GPU provider gate | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 on-demand | 2026-09-14 | user decision | selected as Phase 11 default; Azure A100/A10 opportunistic only |
| RunPod account control | prod /ml | Restricted `RUNPOD_API_KEY`; auto top-ups disabled | 2026-09-14 visual verification | user-provided RunPod billing screenshot | verified; secret value is never recorded here |
| Infisical secret creation | prod /ml | VLLM_API_KEY (random 32-byte value; never record it here) | 2026-09-14 runtime injection | `vllm-bench-f810b57a7b8ae05a` | verified without recording the secret value |
| Dataset checksum | local `.local/aml_data` | HI-Small_Trans.csv | 2026-09-13 | `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| Dataset checksum | local `.local/aml_data` | HI-Medium_Trans.csv | 2026-09-13 | `3126afb8155e7c8815d62bc5370549a7b5ae6bf7dfe872b3cd7813e66a3d7ff5` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| Dataset checksum | local `.local/aml_data` | LI-Medium_Trans.csv | 2026-09-13 | `0fc89584453c97472b1bfde7ca746e21d7167140c22a6c4875272b197a4a3910` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| Azure quota | westus3 | Standard DASv5 Family vCPUs | 2026-09-15 read | — | current limit 0; blocks `Standard_D2as_v5`; no request raised |
| Azure quota | westus3 | Total regional low-priority/Spot vCPUs | 2026-09-15 read | — | current limit 3; blocks the Spot user pool |
| Azure quota | westus3 | Standard DASv4 Family vCPUs | 2026-09-15 read | — | current limit 10; supports `Standard_D2as_v4` (chosen SKU) |
| AKS SKU decision | westus3 | `Standard_D2as_v4` pay-as-you-go user pool | 2026-09-15 | user decision | selected; **no quota request is required** — a Spot increase would not lift the DASv5 zero family limit |
| Azure provider registration | subscription-wide | `Microsoft.App` (Container Apps) | 2026-09-15 | `az provider show -n Microsoft.App` => `Registered` | registered on explicit owner approval; free, idempotent, creates no resource and must precede the Container Apps apply |
| GitHub deploy gate | repo settings | `Production` environment required reviewer (`Kartik-Hirijaganer`) | 2026-09-15 verified | run `34994335606` | configured; blocking observed on a plan-only dispatch, run cancelled unapproved, `AKS_DEPLOY_ENABLED` disarmed |

## Resource sessions

| Date | Provider | SKU | Purchase option | Start (UTC) | Stop (UTC) | Hours | Quoted rate USD/hour | Projected cost USD | Actual cost USD | Run ID | Evidence run IDs | Budget scope | Allocation | Teardown verified |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| 2026-07-14 | local | developer workstation | owned | — | — | 0 | 0 | 0 | 0 | gfp-c41b1fbb266f44d4 | — | historical | historical | not-applicable |
| 2026-08-17 | OpenRouter | multi-model SAR evaluation | metered API | — | — | 0 | 0 | 7.600000 | 5.486233 | sar-eval-e5c9a36b5f8a33f3 | — | historical | historical | not-applicable |
| 2026-09-14 | Azure | Standard_E16ads_v5 | pay-as-you-go | 2026-09-14T01:49:31Z | 2026-09-14T17:35:34Z | 9.955459 | 1.048000 | 3.235985 | — | data-batch-20260914-pilot1 | fulldata-b55c4ae63ed8bbae | current-plan | azure_cpu_batch | yes |
| 2026-09-14 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-14T21:59:09Z | 2026-09-15T04:01:53Z | 6.045556 | 0.740000 | 5.920000 | — | vllm-bench-f810b57a7b8ae05a | vllm-e2e-61dcda4aef97b74a | current-plan | gpu_benchmark | yes |
| 2026-09-16 | Azure | Standard_B2s + 2×Standard_D2as_v4 | pay-as-you-go | 2026-09-16T14:05:28Z | 2026-09-16T17:19:25Z | 3.232500 | 0.233600 | 0.790000 | — | aks-demo-20260915-01 | — | current-plan | supporting_resources | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T11:49:15Z | 2026-09-17T13:24:42Z | 3.108600 | 0.740000 | 11.840000 | 2.300000 | vllm-bench-042a265fdc42c9d4 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T14:20:51Z | 2026-09-17T14:50:45Z | 0.498108 | 0.740000 | 5.280000 | — | vllm-bench-445a5c1f412a96c8 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T15:23:43Z | 2026-09-17T15:42:01Z | 0.305000 | 0.740000 | 0.750000 | — | vllm-bench-4c656331f7ce9466 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T15:56:48Z | 2026-09-17T16:23:32Z | 0.572404 | 0.740000 | 2.000000 | — | vllm-bench-5de63d0b0fe17634 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T17:16:14Z | 2026-09-17T17:46:58Z | 0.828652 | 0.740000 | 5.282763 | — | vllm-bench-ff008c8fe1668e25 | vllm-bench-cf06c21fa6cb425d | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T17:55:32Z | 2026-09-17T18:45:10Z | 1.523099 | 0.740000 | 5.920000 | — | vllm-bench-ba468433f6fd893a | vllm-bench-c041bc70ff0d4de5, vllm-bench-3133fb4b74e9a053 | current-plan | gpu_benchmark | yes |

For a current-plan session, `Actual cost USD` may remain `—` only until provider billing lands.
The validator conservatively counts actual cost when present and otherwise projected cost. Start
and stop timestamps use ISO 8601 UTC. A stopped current-plan resource without teardown verification
fails the ledger gate.

The Azure session's hours are the sum of two observed VM activity-log windows
(01:50:06–09:57:27 and 15:45:35–17:35:34 UTC); the intervening deallocated period is excluded.
At the quoted rate those windows estimate $10.43 of VM compute before Azure billing settles. The
projected-cost field retains the pilot admission projection rather than substituting that estimate.
The corrected RunPod AWQ canary stopped before model download because its assigned host lost
outbound routing after dependency setup. Its observed active interval estimates $0.37 at the quoted
rate; `Actual cost USD` remains unset until provider billing settles. It produced no measurements,
and the Pod plus matching volumes were verified absent after deletion.
The egress-gated AWQ canary reached live generation and received HTTP 200 from the pinned model,
but its first production-gated warm-up was rejected for `asserted_fact_mismatch` and
`unmapped_narrative_fact`. The harness misclassified that expected gate verdict as a serving error,
so no measured level started. Its 0.305000-hour active interval estimates $0.23 at the quoted rate;
the exported failure artifact is local, provider billing is pending, and clean teardown found no
matching Pod or volume.
The final-validation attempt completed and exported the AWQ smoke matrix with zero serving errors,
token usage on every attempt, and GPU telemetry. It stopped before the two-endpoint smoke because
the frozen config omitted the BF16 remote-telemetry command, which would have mislabeled AWQ GPU
samples as BF16 evidence. Both Pods were deleted and verified absent. Their summed 0.572404-hour
active interval estimates $0.423579 at the quoted rate; provider billing is pending.
The provenance-fix attempt completed a two-arm raw smoke matrix with 32/32 measured generations,
zero serving errors, token usage on every response, and per-level GPU telemetry. The first cascade
smoke then failed closed before measurement because BF16 immutable GPU identity was still queried
locally from the AWQ Pod even though BF16 load telemetry and startup logs were remote. The observed
driver mismatch (`580.126.20` recorded versus BF16's actual `580.159.04`) proved the evidence would
have been mislabeled. Both Pods and volumes were deleted and verified absent. Their summed active
interval of 0.828652 hours estimates $0.613202 at the quoted rate; provider billing is pending.
The final validation session passed the previous provenance stop condition and produced three live
checkpoints from commit `aaf903114131f74e8b2bffc859427b1ec511d896`: a 32-generation raw smoke
matrix with zero serving errors, a same-GPU 40-case-per-arm development comparison, and an 8-case
production constrained-cascade smoke. The raw development pilot reproduced the 63.5% AWQ model
weight-memory reduction and measured 47.2% higher request throughput plus 25.7% lower p95 at c32,
but BF16 aggregate schema and reference validity were each 0.95. In the production cascade all
eight cases escalated and all eight final BF16 attempts failed the gate; no serving error was
omitted. Scaling only the two constrained scenarios' measured endpoint occupancy to their declared
1,000-case c32 workloads projected $23.130794 before overhead and external API spend, or $30.070032
with the required 30% margin. `experiment_budget.py admit` therefore returned
`projection_exceeds_allocation` against $28.00 and the full matrix was not run. The summed
1.523099-hour Pod interval estimates $1.127093 at the quoted rate; provider billing is pending.
Both Pods and matching volumes were verified absent after deletion. The temporary production-path
LLM ceiling was restored from $22.00 to $0.25 immediately after teardown.
