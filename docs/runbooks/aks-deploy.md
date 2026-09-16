# Azure AKS demonstration

> **Status: executed 2026-09-16, torn down and verified clean.** Paid session
> `aks-demo-20260915-01` applied the Terraform below, deployed the SHA-pinned image, ran
> authenticated smoke and load, captured
> [`aks-hpa-scaling.md`](../reference/benchmarks/aks-hpa-scaling.md) — API replicas 1 → 5 → 1, first
> scale-up 101 s, scale-back 117 s, 100/100 durable runs completed on `v1.35.7` — then destroyed the
> cluster. `make aks-verify-clean` passed and the
> [ledger row](../reference/experiments/ledger.md) reads **teardown verified = yes**. The cluster is
> ephemeral by design: nothing is standing now, and the procedure below is what re-creates it in
> roughly twelve minutes.

The committed kind report stays local evidence for a separate runtime. Only the redacted artifact
from a governed AKS session supports an observed-AKS claim, and each is cited from its own file
(ADR-021).

## Architecture as applied

| Layer | Choice | Bound |
| --- | --- | --- |
| Control plane | AKS Free, Entra RBAC, local accounts/run-command disabled | No control-plane SLA claim |
| System pool | One `Standard_B2s` on-demand node | Critical add-ons only while user pool exists |
| Application pool | `Standard_D2as_v4` on-demand, autoscaling 1–2 | Fits the verified DASv4 quota |
| Network | Azure CNI Overlay + Cilium; operator-CIDR API allowlist | No public node IPs |
| Secrets | Infisical Kubernetes Operator with AKS managed identity | Read-only `/` and `/llm` scopes |
| Evidence | HPA 1→5→1 plus worker process/pod replacement | Synthetic cases only |

The compute-only maximum-pool estimate is derived from the dated rates in
[`budget.yaml`](../../config/experiments/budget.yaml). Disks, load balancer/IP, traffic, and any
enabled monitoring are excluded and must be added to the approved projection.

## Free pre-flight validation

Every one of these is read-only and costs nothing. Run them before asking for a session; a failure
here is a failure that would otherwise surface while the meter is running.

```bash
make tf-validate
make iac-scan
make aks-plan
make k8s-validate
pytest -k deploy
```

`aks-plan` initializes with `-backend=false`, disables refresh, and never applies. It resolves the
signed-in account, Entra operator object id, public operator CIDR, and budget contact into
`TF_VAR_*` without committing them. An approved apply copies `backend.tf.template` into place and
uses the isolated `aks-demo.terraform.tfstate` key in the existing state container — which is how
the executed session ran, and why `aks-verify-clean` can assert after a destroy that the state holds
no managed resources.

## Approved bounded lifecycle

This is the sequence session `aks-demo-20260915-01` executed, and the sequence any repeat run
follows. Each line is a separate approval; only after a human approves the billable session:

```bash
make aks-plan
CONFIRM=yes make aks-up
CONFIRM=yes make aks-credentials
CONFIRM=yes make aks-operator-install
CONFIRM=yes make aks-secrets-operator
CONFIRM=yes make aks-deploy IMAGE_REF=ghcr.io/kartik-hirijaganer/fraudlens-backend@sha256:<digest>
CONFIRM=yes make aks-smoke
CONFIRM=yes make aks-hpa-demo
make hpa-evidence-validate EVIDENCE=docs/reference/benchmarks/aks-hpa-scaling.json
CONFIRM=yes make aks-down
make aks-verify-clean
```

Before deploy, create the narrowly scoped Infisical Azure identity named by
`INFISICAL_AKS_IDENTITY_ID`; allow the Terraform output `kubelet_identity_object_id` and read-only
access to the actual `prod` paths `/` and `/llm`. The manifests replace both operator identity
placeholders only in a disposable render tree. If the operator path is unavailable, inject the same
secret sets into the process and run `CONFIRM=yes make aks-secrets-sync`; values travel over stdin
and are never printed.

Every Kubernetes mutation verifies a non-kind context and requires confirmation. The workflow adds
a second gate: `AKS_DEPLOY_ENABLED=true`, workflow dispatch, the exact phrase
`deploy-fraudlens-aks-demo-and-destroy`, and an environment approval. One runner discovers its own
temporary API-server `/32`, builds one SHA-tagged GHCR image, applies the reviewed root, deploys it,
injects a short-lived JWT into a namespaced Secret over stdin, runs authenticated smoke and load,
uploads evidence even after a failed proof, and always destroys the paid session.

`AKS_ADMIN_OBJECT_ID` retains the human operator's emergency access. `AKS_DEPLOY_OBJECT_ID` is the
object id—not the client/app id—of the GitHub OIDC service principal; Terraform grants both
principals the AKS RBAC Cluster Admin role so the same runner that applies the cluster can operate
and tear it down without local-account credentials.

## Teardown and verification

Stopping suspends node compute; it is not teardown. After evidence export and explicit permission:

```bash
CONFIRM=yes make aks-down
make aks-verify-clean
uv run python scripts/experiment_budget.py ledger-check
```

The rescue-destroy action requires the same typed phrase as creation. `aks-verify-clean` fails if
either scoped resource group, any prefixed/tagged resource, the subscription budget, or any
Terraform-managed AKS resource remains. Do not mark the ledger row verified before this query
passes.
