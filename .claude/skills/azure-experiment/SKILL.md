---
name: azure-experiment
description: Operate the FraudLens ephemeral Azure data-batch experiment through budget admission, pilot projection, checkpointed execution, artifact export, ledger reconciliation, and teardown.
---

# Azure Experiment

Run the full-data CPU experiment as a bounded, recoverable resource session.

## When To Use

Use for the Azure data-batch VM, full-data pilot or training run, budget admission, spot recovery,
artifact export, or experiment teardown.

## Rules

- No cloud mutation, Blob upload, or teardown occurs without explicit human permission.
- The $15 CPU allocation and repository-wide budget remain hard ceilings; never borrow silently from
  the GPU or reserve allocations.
- Pilot first, project with the configured margin, then pause for the human admission decision.
- The VM receives no application database or LLM credentials; Blob access uses managed identity.
- Checkpoint every stage and keep both platform auto-shutdown and in-VM watchdogs active.

## Steps

1. Read the applicable phase under `plans/`; run `make data-batch-plan` and review resource names,
   SKU, Spot eviction policy, network exposure, storage scope, shutdown, watchdog, and cost estimate.
2. Open the ledger session and run `scripts/experiment_budget.py ledger-check`.
3. After explicit permission, run `CONFIRM=yes make data-batch-up`; request separate permission for
   `make data-batch-upload`.
4. On the VM, run the 1M-row then 5M-row pilots through ingest, features, parity, and one training
   pass. Export pilot measurements and compute the full projection.
5. Run `scripts/experiment_budget.py admit --allocation azure_cpu_batch`; present the projection and
   pause for explicit pilot-to-full approval.
6. Run the full checkpointed stages in the planned order. After a spot eviction, use
   `make data-batch-start` only with permission and resume the same stage/run identity.
7. Export model bundles, manifests, and reports; download and validate them locally before any
   publication or candidate registration.
8. Obtain teardown permission, run `make data-batch-down`, then
   `make data-batch-verify-clean`; complete the ledger with hours, rate, projection, verification,
   and actual cost when Azure billing settles.

## Verification

- Budget admission passes with the configured margin and all other allocations intact.
- Source reconciliation, real-data parity, temporal folds, calibration-derived thresholds, candidate
  identity, and artifact hashes validate locally.
- Checkpoints and uploaded artifacts use the same run id and configuration hash.
- The ledger covers the resource session and `data-batch-verify-clean` reports no resource group,
  disk, IP, NIC, VM, or storage residue.

## Never Do

- Never skip the pilot, self-approve the full run, or reduce the promised dataset to fit cost.
- Never rely on operating-system shutdown as proof that Azure billing stopped.
- Never place secrets in tfvars, `.env`, VM files, logs, commands, reports, or ledger rows.
- Never publish partial output as a demonstrated full-data result.
