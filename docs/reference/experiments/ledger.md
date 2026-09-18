# Experiment Budget Ledger

This ledger is the auditable spend record for bounded FraudLens experiments. A paid run needs a
current-plan row before admission, an allocation from
[`config/experiments/budget.yaml`](../../../config/experiments/budget.yaml), and `yes` in
**Teardown verified** after its ephemeral resources stop. Historical rows exist only so every
published study run remains traceable; they do not consume the new plan's $75 ceiling.
When a resource session produces an aggregate with a distinct logical run ID, **Evidence run IDs**
binds that report to the session without recording or charging the same spend twice.
Zero-cost local sessions are recorded too. A published report with no row is invisible to
`ledger-check`, and "this one was free" is a claim the ledger should carry rather than one a
reader has to infer from an absence (release 0.5.0 Phase 5).

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
| 2026-09-16 | local | developer workstation (kind) | owned | — | — | 0 | 0 | 0 | 0 | k8s-demo-d1dfd6be47b2f876 | — | historical | historical | not-applicable |
| 2026-09-14 | Azure | Standard_E16ads_v5 | pay-as-you-go | 2026-09-14T01:49:31Z | 2026-09-14T17:35:34Z | 9.955459 | 1.048000 | 3.235985 | — | data-batch-20260914-pilot1 | fulldata-b55c4ae63ed8bbae | current-plan | azure_cpu_batch | yes |
| 2026-09-14 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-14T21:59:09Z | 2026-09-15T04:01:53Z | 6.045556 | 0.740000 | 5.920000 | — | vllm-bench-f810b57a7b8ae05a | vllm-e2e-61dcda4aef97b74a | current-plan | gpu_benchmark | yes |
| 2026-09-16 | Azure | Standard_B2s + 2×Standard_D2as_v4 | pay-as-you-go | 2026-09-16T14:05:28Z | 2026-09-16T17:19:25Z | 3.232500 | 0.233600 | 0.790000 | — | aks-demo-20260915-01 | — | current-plan | supporting_resources | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T11:49:15Z | 2026-09-17T13:24:42Z | 3.108600 | 0.740000 | 11.840000 | 2.300000 | vllm-bench-042a265fdc42c9d4 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T14:20:51Z | 2026-09-17T14:50:45Z | 0.498108 | 0.740000 | 5.280000 | — | vllm-bench-445a5c1f412a96c8 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T15:23:43Z | 2026-09-17T15:42:01Z | 0.305000 | 0.740000 | 0.750000 | — | vllm-bench-4c656331f7ce9466 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T15:56:48Z | 2026-09-17T16:23:32Z | 0.572404 | 0.740000 | 2.000000 | — | vllm-bench-5de63d0b0fe17634 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T17:16:14Z | 2026-09-17T17:46:58Z | 0.828652 | 0.740000 | 5.282763 | — | vllm-bench-ff008c8fe1668e25 | vllm-bench-cf06c21fa6cb425d | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T17:55:32Z | 2026-09-17T18:45:10Z | 1.523099 | 0.740000 | 5.920000 | — | vllm-bench-ba468433f6fd893a | vllm-bench-c041bc70ff0d4de5, vllm-bench-3133fb4b74e9a053 | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T20:44:13Z | 2026-09-17T20:55:11Z | 0.182778 | 0.740000 | 2.000000 | — | vllm-bench-4ded2e5f759f7e82 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T21:29:01Z | 2026-09-17T22:00:55Z | 0.906908 | 0.740000 | 1.850000 | — | vllm-bench-0a721bc3b5831216 | vllm-bench-75fb4e0c6dfd6884 | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T22:42:03Z | 2026-09-17T23:00:03Z | 0.299929 | 0.740000 | 1.000000 | — | vllm-bench-82cddfec5c550250 | — | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T23:12:23Z | 2026-09-17T23:41:40Z | 0.841213 | 0.740000 | 0.650000 | — | vllm-bench-39007a09a3c83bcb | vllm-bench-2499598f2617bc6b | current-plan | gpu_benchmark | yes |
| 2026-09-17 | RunPod Secure Cloud | 2×NVIDIA GeForce RTX 4090 | on-demand | 2026-09-17T23:50:07Z | 2026-09-18T03:18:20Z | 5.006000 | 0.740000 | 2.873566 | — | vllm-bench-be12675628805a53 | — | current-plan | gpu_benchmark | yes |

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
The protocol-v4 compatibility session completed three live checks. The unconstrained AWQ and BF16
smokes each made 24 successful generations with complete token and telemetry capture but passed
the production gate 0/24. The constrained production cascade passed 8/8 at the AWQ tier with no
escalation and reference validity 1.0, but its 522.8445-second p95 made it unsuitable for the
latency claim. AWQ used 5.2036 GiB of model-weight memory versus 14.2488 GiB for BF16, preserving
the 63.5% reduction. The summed 0.906908 endpoint-hours estimate $0.671112 at the quoted rate;
provider billing is pending. Both Pods and matching volumes were deleted and independently
verified absent.
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
The protocol-v3 remediation session `vllm-bench-4ded2e5f759f7e82` was opened before provisioning
with a $2.00 projection. Its AWQ smoke completed 24 generations with zero serving errors, complete
token usage, and GPU telemetry, but all 24 failed the production gate as `schema_invalid`.
Diagnosis retained config/corpus/run hashes and isolated the prompt-shape plus vLLM `uniqueItems`
contract defects. The Pod and volume were deleted and verified absent after 0.182778 hours, about
$0.135256 at the quoted rate pending provider settlement. Run artifact SHA-256:
`9b57aae90600d93340167efb0aa3d017526844bc3976ed0abddb38551baadfc3`.
Protocol-v4 session `vllm-bench-0a721bc3b5831216` was opened with a $1.85 projection and closed
after an estimated $0.671112 with verified teardown. Its full corpus SHA-256 is
`abca4898c5101e62cbff9348c37eb2e7712bc6970d3e4aaf4d5256f399612447`.
Protocol-v5 AWQ canary `vllm-bench-82cddfec5c550250` is opened with a $1.00 projection. One more
dollar moved from reserve to `gpu_benchmark`: allocation $33.00, reserve $2.00, overall ceiling
unchanged at $75.00. Its full corpus SHA-256 is
`2e1df4db3099fcd411158fd18d1a6bcb9cf69bf1dadef8138d68712a049376e2`.
The canary completed the constrained c32 smoke at 8/8 gate pass and zero serving errors, but p95
was 441.57035 seconds. The compact unconstrained c1 control then passed 8/8 with p95 2.616 seconds.
Its subsequent c8/c32 checkpoints were invalidated because the production cache reused the c1
responses; they are retained in the exported run but must not be reported. Run artifact SHA-256:
`34634cf87b3e4c487dc931708237ed0544824fa5f7b29866eeb9f83dcfbf5b18`. The 0.299929-hour
session estimates $0.221947 at the quoted rate pending settlement. The Pod and matching volume
were deleted and independently verified absent.
Protocol-v5 cache-isolated session `vllm-bench-39007a09a3c83bcb` completed smoke plus the
40-case development evidence run `vllm-bench-2499598f2617bc6b` from commit `e096589`. AWQ and
BF16 each completed c1/c8/c32 with zero serving errors; the live c32 cascade passed 40/40 with
zero escalation because AWQ passed every c32 case. AWQ c32 p95 was 13.7104 seconds versus
16.62465 for BF16, and cascade p95 was 13.4459 seconds. The development projection for the seven
admitted 1,000-case levels is $2.210435, or $2.873566 with the required margin. Smoke artifact
SHA-256: `6d9801ae1233adaf2aabffedc51525237ab676237c2cc4b2a4617e40669fcc57`;
development artifact SHA-256:
`de39efdd4d3fd19b48222f415d2870f48429a6eabbfc237a1290dcaaa087c102`. The session consumed
0.841213 summed endpoint-hours, about $0.622497 pending settlement, and clean teardown found no
matching Pod or volume for either role.
Full-run session `vllm-bench-be12675628805a53` completed all seven admitted 1,000-case levels from
commit `615285e0e63f85eb2ee0661ae21bd36a4cbf8f8d`: AWQ and BF16 at c1/c8/c32 plus the
unconstrained production cascade at c32. The cascade served 997/1,000 cases (99.7%) versus
995/1,000 for BF16 alone, escalated 92 cases, made 1,089 model calls with zero serving errors, and
measured 17.18425-second p95 versus BF16's 16.1639 seconds (+6.3%). Accepted drafts had reference
validity 1.0. Three evidence-free cases failed preflight before any model call. AWQ model weights
remained 63.5% smaller. The canonical coordinator and both exported run manifests are byte-identical
at SHA-256 `4ba9307f1a2c8bbe059d2e402f94e2c4e16f61e251a303fea297976da908fd6a`.
The approximately 5.006 summed endpoint-hours estimate $3.70 at the quoted rate, pending provider
settlement. This exceeded the $2.873566 margin-bound projection because the admission model omitted
idle second-endpoint time during sequential raw levels, but remained inside the $37 allocation.
Both Pods and matching volumes were deleted and independently verified absent. Four dollars had
moved from unused `supporting_resources` to `gpu_benchmark`; the overall $75 ceiling and $2 reserve
remain unchanged.
Release 0.5.0 closed without running the Tier-3 external-model pilot. Its case set, candidates,
thresholds, and selection rule stay frozen in
[`config/experiments/sar-tier3-pilot.yaml`](../../../config/experiments/sar-tier3-pilot.yaml) and
are asserted by `test_sar_tier3_pilot.py`, but no hosted candidate was invoked, no spend was
incurred, and no row is opened. The 88-plus-12 composition therefore never needed the owner's
top-up confirmation: that question arises only when the pilot is admitted. The external tier ships
configured and unmeasured, and the published cascade report claims nothing about it
([ADR-030](../../architecture/adr/ADR-030-quality-gated-sar-model-cascade.md)).
The zero-cost kind session `k8s-demo-d1dfd6be47b2f876` is recorded above so its published HPA and
durability evidence reconciles against this ledger. It created no cloud resource; its run id is
derived from the commit, config hash, and generation time of the artifact it produced.

