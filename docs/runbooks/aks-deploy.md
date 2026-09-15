# Azure AKS demonstration

Release 0.3 validates this architecture but does **not** apply it. Its measured Kubernetes evidence
comes from kind. A real AKS apply, workload proof, and teardown are a separately approved
next-release experiment governed by ADR-021 and ADR-028.

## Validated architecture

| Layer | Choice | Bound |
| --- | --- | --- |
| Control plane | AKS Free, Entra RBAC, local accounts/run-command disabled | No control-plane SLA claim |
| System pool | One `Standard_B2s` on-demand node | Critical add-ons only while user pool exists |
| Application pool | `Standard_D2as_v5` Spot, autoscaling 1–2 | Eviction-tolerant durable workers |
| Network | Azure CNI Overlay + Cilium; operator-CIDR API allowlist | No public node IPs |
| Secrets | Infisical Kubernetes Operator with AKS managed identity | Read-only `/backend` and `/llm` scopes |
| Evidence | HPA 1→5→1 plus worker process/pod replacement | Synthetic cases only |

The compute-only maximum-pool estimate is derived from the dated rates in
[`budget.yaml`](../../config/experiments/budget.yaml). Disks, load balancer/IP, traffic, and any
enabled monitoring are excluded and must be added to the approved projection.

## Free release-0.3 validation

```bash
make tf-validate
make iac-scan
make aks-plan
make k8s-validate
pytest -k deploy
```

`aks-plan` initializes with `-backend=false`, disables refresh, and never applies. It resolves the
signed-in account, Entra operator object id, public operator CIDR, and budget contact into
`TF_VAR_*` without committing them. The committed root uses the isolated
`aks-demo.terraform.tfstate` key when an approved apply later uses remote state.

## Approved next-release lifecycle

Only after a human approves the billable session:

```bash
CONFIRM=yes make aks-up
CONFIRM=yes make aks-credentials
CONFIRM=yes make aks-operator-install
CONFIRM=yes make aks-secrets-operator
CONFIRM=yes make aks-deploy IMAGE_TAG=<immutable-commit-sha>
CONFIRM=yes make aks-smoke
CONFIRM=yes make aks-hpa-demo
CONFIRM=yes make aks-stop
```

Before deploy, create the narrowly scoped Infisical Azure identity named by
`INFISICAL_AKS_IDENTITY_ID`; allow the Terraform output `kubelet_identity_object_id` and read-only
access to `prod` paths `/backend` and `/llm`. The manifests replace both operator identity
placeholders only in a disposable render tree. If the operator path is unavailable, inject the same
secret sets into the process and run `CONFIRM=yes make aks-secrets-sync`; values travel over stdin
and are never printed.

Every Kubernetes mutation verifies a non-kind context and requires confirmation. The workflow adds
a second gate: `AKS_DEPLOY_ENABLED=true`, workflow dispatch, and an environment approval. It builds
one SHA-tagged GHCR image, applies the reviewed root, deploys that image, runs smoke before evidence,
uploads evidence as an artifact, and optionally stops the cluster.

## Teardown and verification

Stopping suspends node compute; it is not teardown. After evidence export and explicit permission:

```bash
CONFIRM=yes make aks-down
make aks-verify-clean
uv run python scripts/experiment_budget.py ledger-check
```

The destroy workflow additionally requires the phrase `destroy-fraudlens-aks-demo`.
`aks-verify-clean` fails if either scoped resource group, any prefixed/tagged resource, or the
subscription budget remains. Do not mark the ledger row verified before this query passes.
