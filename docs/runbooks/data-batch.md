# Azure data-batch experiment

This runbook operates the release 0.3 full-data CPU experiment as one bounded Azure resource
session. The infrastructure is validated and plan-only until a human explicitly approves each
cloud mutation. It handles synthetic IBM AML data only; the VM receives no application database,
LLM, or Infisical credential.

## Frozen decision inputs

Read-only checks on 2026-09-14 established the current West US 3 path:

| Input | Current value | Consequence |
| --- | ---: | --- |
| Total regional standard vCPUs | 72 | A 16-vCPU host fits. |
| Standard EADSv5 family vCPUs | 32 | `Standard_E16ads_v5` fits. |
| Total regional low-priority/Spot vCPUs | 3 | Spot cannot admit a 16-vCPU host. |
| E16ads v5 Linux PAYG | $1.048/hour | PAYG is the Phase 6 default. |
| E16ads v5 Linux Spot | $0.193670/hour | Provenance retained, but unusable at current quota. |

The price comes from the [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices), filtered by `armRegionName = westus3`,
`armSkuName = Standard_E16ads_v5`, `serviceName = Virtual Machines`, Linux Eadsv5 product, and
Consumption price type. The quote and provider effective date are frozen in
[`config/experiments/budget.yaml`](../../config/experiments/budget.yaml).

The two-pilot scheduling envelope is two compute hours: **$2.096** before margin and **$2.7248**
with the required 30% margin. The preliminary three-to-five-hour full-session envelope is
$3.144–$5.240 compute-only, or $4.0872–$6.8120 with margin. Only measurements from the 1M- and
5M-row pilots can admit the full run; the $15 CPU allocation remains the hard ceiling.

## What Terraform plans

The `data-batch` root creates 16 resources in or scoped to one ephemeral session:

| Area | Resources | Control |
| --- | --- | --- |
| Session | Resource group | One destroy scope; ephemeral/run-id tags. |
| Network | VNet, subnet, NSG, NSG association, static Standard public IP, NIC | Inbound TCP/22 only from the resolved operator CIDR. |
| Compute | E16ads v5 VM, Premium 128-GiB OS disk (part of the VM), shutdown schedule, self-deallocate RBAC assignment | SSH key only; secure boot/vTPM; system identity; PAYG. |
| Storage | LRS account, private container, lifecycle policy, two container-scoped Blob Data Contributor assignments | Shared keys off; OAuth default; VM and operator identities only; deny-by-default firewall; 30-day expiry. |
| Cost | Subscription budget filtered to the resource group | 50/80/100% actual and 100% forecast alerts. |

The VM's local NVMe resource disk is mounted by Azure at `/mnt`; cloud-init creates
`/mnt/fraudlens` for DuckDB spill and Parquet work. Persistent checkpoints remain on the Premium OS
disk and are synced to Blob after each stage.

## Free validation and STOP 1

Run only:

```bash
make tf-validate
make iac-scan
make data-batch-plan
make data-batch-verify-clean
```

`data-batch-plan` reads the signed-in Azure account, resolves the current public IPv4 address,
loads `~/.ssh/id_ed25519.pub`, and derives the operator identity and budget contact without echoing
or committing those values. Override them with `TF_VAR_operator_cidr`,
`TF_VAR_operator_principal_id`, `TF_VAR_ssh_public_key`, and `TF_VAR_budget_contact_emails` when
required. It initializes with `-backend=false`; it cannot apply.

Stop here and obtain explicit permission before either provisioning or Blob upload.

## Approved provisioning and upload

Choose a stable session id, open its row in the
[experiment ledger](../reference/experiments/ledger.md), and use the same id through upload,
execution, reports, and teardown:

```bash
CONFIRM=yes make data-batch-up RUN=data-batch-<session>
make data-batch-watchdog
CONFIRM=yes make data-batch-upload RUN=data-batch-<session>
make data-batch-ssh
```

Provisioning and upload require separate human approvals. `data-batch-up` generates the remote
backend from its committed template, creates a fresh reviewed plan, and applies only that saved
plan. Upload uses Azure AD/OAuth and writes the two Medium CSVs under `input/<run-id>/`.

## Pilot and admission gate

Inside a `tmux` session on the VM, clone the exact approved commit, download the run's inputs with
managed identity, and run the frozen pipeline:

```bash
uv sync --all-packages --group fulldata
uv run --group fulldata python scripts/fulldata.py pilot --rows 1000000
uv run --group fulldata python scripts/fulldata.py pilot --rows 5000000
uv run --group fulldata python scripts/fulldata.py estimate \
  --rate azure_e16ads_v5_payg \
  --pilot-hours <measured-hours> \
  --pilot-rows 5000000 \
  --target-rows 63149721
uv run python scripts/experiment_budget.py admit \
  --allocation azure_cpu_batch \
  --rate azure_e16ads_v5_payg \
  --pilot-hours <measured-hours> \
  --pilot-units 5000000 \
  --target-units 63149721
```

Record parity, peak RSS, stage throughput, elapsed time, and the 30%-margin cost projection. Stop
again for explicit approval before processing all Medium rows. Do not reduce the row count to make
the admission pass.

## Shutdown, teardown, and proof

Four controls bound the resource session:

1. Azure platform auto-shutdown is calculated as plan time plus eight hours.
2. An in-VM systemd timer deallocates the VM eight hours after boot through its system identity.
3. The $15 Azure budget emits escalating actual and forecast notifications.
4. The ledger records the session deadline, projected cost, settled cost, and teardown evidence.

Guest operating-system shutdown does not stop compute billing. After artifacts have been exported
and validated locally, obtain teardown permission and run:

```bash
make data-batch-download RUN=data-batch-<session>
CONFIRM=yes make data-batch-down RUN=data-batch-<session>
make data-batch-verify-clean
uv run python scripts/experiment_budget.py ledger-check
```

`data-batch-verify-clean` fails if the resource group, any prefixed/tagged Azure resource, or the
subscription budget remains. Record `Teardown verified = yes` only after this check passes.
