# Experiment Budget Ledger

This ledger is the auditable spend record for bounded FraudLens experiments. A paid run needs a
current-plan row before admission, an allocation from
[`config/experiments/budget.yaml`](../../../config/experiments/budget.yaml), and `yes` in
**Teardown verified** after its ephemeral resources stop. Historical rows exist only so every
published study run remains traceable; they do not consume the new plan's $75 ceiling.

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
| Azure quota | eastus | Standard NCADS_A100_v4 Family vCPUs = 24 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus2 | Standard NCADS_A100_v4 Family vCPUs = 24 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus | Standard NVADSA10v5 Family vCPUs = 36 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus2 | Standard NVADSA10v5 Family vCPUs = 36 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus | Standard EADSv5 Family vCPUs = 32 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus2 | Standard EADSv5 Family vCPUs = 32 | 2026-09-13 read | — | current limit 0; request pending |
| Azure quota | eastus | Low-priority/Spot vCPUs >= 36 | 2026-09-13 read | — | current limit 3; request pending |
| Azure quota | eastus2 | Low-priority/Spot vCPUs >= 36 | 2026-09-13 read | — | current limit 3; request pending |
| Subscription offer verification | Azure subscription | Pay-as-you-go supports Spot | — | — | pending |
| Infisical secret creation | prod /ml | VLLM_API_KEY (random 32-byte value; never record it here) | 2026-09-13 read | — | not verified; suppressed CLI lookup returned non-zero |
| Dataset checksum | local `.local/aml_data` | HI-Small_Trans.csv | 2026-09-13 | `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| Dataset checksum | local `.local/aml_data` | HI-Medium_Trans.csv | 2026-09-13 | `3126afb8155e7c8815d62bc5370549a7b5ae6bf7dfe872b3cd7813e66a3d7ff5` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| Dataset checksum | local `.local/aml_data` | LI-Medium_Trans.csv | 2026-09-13 | `0fc89584453c97472b1bfde7ca746e21d7167140c22a6c4875272b197a4a3910` | verified; copy into `config/fulldata.yaml` in Phase 5 |
| GPU fallback account | RunPod | Create only if the fallback gate is triggered | — | — | not triggered |

## Resource sessions

| Date | Provider | SKU | Purchase option | Start (UTC) | Stop (UTC) | Hours | Quoted rate USD/hour | Projected cost USD | Actual cost USD | Run ID | Budget scope | Allocation | Teardown verified |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 2026-07-14 | local | developer workstation | owned | — | — | 0 | 0 | 0 | 0 | gfp-c41b1fbb266f44d4 | historical | historical | not-applicable |
| 2026-08-17 | OpenRouter | multi-model SAR evaluation | metered API | — | — | 0 | 0 | 7.600000 | 5.486233 | sar-eval-e5c9a36b5f8a33f3 | historical | historical | not-applicable |

For a current-plan session, `Actual cost USD` may remain `—` only until provider billing lands.
The validator conservatively counts actual cost when present and otherwise projected cost. Start
and stop timestamps use ISO 8601 UTC. A stopped current-plan resource without teardown verification
fails the ledger gate.
