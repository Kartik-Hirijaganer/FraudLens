# ADR-021 — AKS as the ephemeral Kubernetes demonstration runtime

- **Status:** Accepted
- **Date:** 2026-09-14
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  [`plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md`](../../../plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md)

## Context

FraudLens needs portable Kubernetes manifests, horizontal autoscaling evidence, and durable worker
recovery without misrepresenting release 0.3 as a live Azure deployment. ADR-007 still selects
Azure Container Apps as the application deployment target and switch path. AKS is a separate,
ephemeral demonstration runtime whose paid apply is deferred to the next release.

The proof must distinguish infrastructure deployability from observed behavior. Local kind can
measure HPA scale-out/convergence and worker replacement at zero cloud cost. Terraform and an inert
workflow can validate the Azure shape without creating a cluster.

## Decision

Use one Kustomize base for kind and AKS. Release 0.3 publishes measured kind evidence and validates
an AKS Terraform root with a Free control plane, one `Standard_B2s` system node, and a bounded
one-to-two-node `Standard_D2as_v5` Spot user pool. Azure CNI Overlay with Cilium is the AKS network
data plane and policy engine. The public API endpoint is restricted to operator-provided CIDRs;
local accounts and AKS run-command are disabled; Entra RBAC, OIDC, and workload identity are enabled.

The API HPA uses CPU at 60%, `minReplicas: 1`, and `maxReplicas: 5`. The minimum is deliberate:
standard HPA does not provide scale-to-zero. Durable investigation work uses PostgreSQL leases and
fencing, so replacing a worker may repeat a bounded attempt without duplicating committed outputs.
Infisical remains the secret source of truth; its Kubernetes operator authenticates with the AKS
managed identity and writes namespaced Secrets at runtime.

`deploy-aks.yml` is dispatch-only and every cloud job is gated by `AKS_DEPLOY_ENABLED=true`.
Creation, workload mutation, stop, and destroy commands also require explicit confirmation. No AKS
apply or AKS runtime evidence is part of release 0.3.

## Evidence

- [`k8s-hpa-scaling.md`](../../reference/benchmarks/k8s-hpa-scaling.md) records the measured local
  autoscaling and worker-recovery proof.
- [`deploy/k8s/`](../../../deploy/k8s/) is schema-validated and security-scanned.
- [`infra/terraform/environments/aks-demo/`](../../../infra/terraform/environments/aks-demo/) is
  formatter-, provider-, plan-, and Checkov-validated without an apply.
- [`deploy-aks.yml`](../../../.github/workflows/deploy-aks.yml) encodes the next-release lifecycle
  and clean-teardown path while remaining feature-gated.

## Options considered and rejected

1. **Claim a real AKS deployment in release 0.3** — rejected because no approved apply or measured
   AKS session exists.
2. **Replace Container Apps with AKS now** — rejected because it broadens operational cost and
   support scope without a release requirement; ADR-007 remains active.
3. **Use KEDA for scale-to-zero** — rejected for this demonstration because it adds an operator and
   queue-scaling contract. One minimum API replica keeps the proof understandable and bounded.
4. **Run only kind and omit AKS IaC** — rejected because local behavior alone does not prove the
   Azure architecture is deployable.
5. **Use a permanent AKS cluster** — rejected because continuing node and observability charges
   conflict with the personal-project budget.

## Tradeoffs accepted

- kindnet does not enforce the committed NetworkPolicies; the release proves their structure, while
  AKS will enforce them through Cilium in the approved run.
- A public, CIDR-restricted API is less isolated than a private cluster but avoids private DNS and
  operator-network cost for a short-lived synthetic-data demonstration.
- Spot application nodes reduce cost but may be evicted. Durable leases and a critical on-demand
  system pool make that failure mode explicit rather than pretending exactly-once execution.
- `minReplicas: 1` incurs compute while the cluster runs. The lifecycle therefore includes stop,
  destroy, budget alerts, and read-only residue verification.

## Reconsider when

- AKS becomes the primary application target rather than a demonstration runtime.
- Private connectivity, regulated data, or real PHI enters the scope.
- Queue depth becomes the appropriate scaling signal and KEDA's scale-to-zero savings exceed its
  operational cost.
- Spot eviction rates prevent the durability SLO or a Standard control-plane SLA is required.

Never cite the kind evidence as an observed AKS deployment. The supported release claim is:
“Deployable to Azure AKS with Terraform/HPA; autoscaling and durability proven on Kubernetes using
kind.”
