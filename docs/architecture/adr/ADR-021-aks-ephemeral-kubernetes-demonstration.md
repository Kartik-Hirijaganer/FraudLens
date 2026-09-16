# ADR-021 — AKS as the ephemeral Kubernetes demonstration runtime

- **Status:** Accepted; amended 2026-09-16 (see [Amendment](#amendment--2026-09-16-release-040))
- **Date:** 2026-09-14
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  `plans/2026-09-13-vllm-awq-benchmark-fulldata-training-and-aks-deployment.md` (retired; see plans/README.md)

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

## Amendment — 2026-09-16 (release 0.4.0)

Release 0.4.0 executed the apply this record deferred, under
[`plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md`](../../../plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md).
The decision is unchanged in substance: AKS remains the ephemeral demonstration runtime, ADR-007
keeps Container Apps as the application target, and the cluster is still created and destroyed per
session. Three specifics moved, each because the subscription would not create what this record
originally specified.

**User-pool SKU: `Standard_D2as_v5` → `Standard_D2as_v4`.** `az vm list-usage -l westus3` reports
`Standard DASv5 Family vCPUs = 0`. The SKU lists as available in the region, but availability is not
quota — any node pool using it fails with `QuotaExceeded`. **No quota request would lift this:** a
Spot increase does not raise a zero *family* limit, so the family itself had to change.
`Standard_D2as_v4` is the cheapest reliable substitute that has quota (DASv4 = 10) at the same
2 vCPU shape. `Standard_B2ms` is marginally cheaper and was rejected because burstable CPU credits
throttle under sustained load, and this session exists to produce a defensible CPU-driven HPA
measurement. The system pool stays `Standard_B2s`.

**User-pool priority: Spot → Regular (on-demand).** Total regional low-priority vCPUs in westus3 is
**3**, and the two-node pool needs 4 — a bound that holds regardless of SKU. On-demand consumes 4 of
72 regional vCPUs, removes mid-session eviction from the proof, and leaves node scale-out genuinely
demonstrable. The module keeps Spot opt-in behind `user_pool_spot_enabled = false`
([`modules/aks/main.tf`](../../../infra/terraform/modules/aks/main.tf)), emitting the eviction
policy and `scalesetpriority` taint only when Spot is requested, so the accepted tradeoff above —
"Spot application nodes reduce cost but may be evicted" — did not apply to this session.

**External access: a `LoadBalancer` Service in the `aks-demo` overlay.** The base Service is
`ClusterIP`, reachable only by port-forward, which leaves the demonstration with no external URL and
no path for an authenticated load generator. The overlay patches a `LoadBalancer` Service
([`overlays/aks-demo/service-lb.yaml`](../../../deploy/k8s/overlays/aks-demo/service-lb.yaml)) with
`externalTrafficPolicy: Local` and an Azure health-probe annotation pointing at `/healthz`, plus an
appended NetworkPolicy ingress rule — source-IP preservation means Internet traffic matches no
`namespaceSelector`, so without that rule Cilium drops every request to a load balancer that
otherwise answers nothing. The base and the kind overlay stay `ClusterIP`. The load balancer and its
public IPs are priced per session in the generated
[cost model](../../reference/cost-model.md).

**Supported claim.** The release-0.3 wording in the closing sentence above is superseded. Measured
AKS evidence now exists — [`aks-hpa-scaling.md`](../../reference/benchmarks/aks-hpa-scaling.md)
records API replicas 1 → 5 → 1, first scale-up 101 s, scale-back 117 s, and 100/100 durable runs
completed on `v1.35.7`, captured during paid session `aks-demo-20260915-01` and followed by a
verified teardown. The supported wording becomes: **"Deployed on Azure AKS with Terraform and HPA;
the cluster is ephemeral by design and re-created on demand."** The prohibition itself stands: kind
evidence is still never cited as an observed AKS deployment, and each runtime is claimed only from
its own artifact.
