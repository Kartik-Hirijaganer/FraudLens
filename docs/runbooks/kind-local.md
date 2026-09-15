# Local Kubernetes demonstration with kind

This zero-cost path proves FraudLens manifest portability, API HPA behavior, and durable worker
recovery. It uses synthetic transactions, local Postgres, the keyless mock SAR drafter, and an
ephemeral two-node kind cluster. It does not create Azure or RunPod resources.

## Prerequisites and safety

Install the pinned tools reported by `make k8s-tools-check`: Docker, kind, kubectl, Helm, Kustomize,
kubeconform, and Trivy. The harness accepts only the exact `kind-<configured-name>` context for kind
mutations. It redacts Secret values and always attempts cluster deletion on exit.

kind uses kindnet, which does not enforce NetworkPolicy. `make k8s-validate` proves policy structure;
the AKS overlay selects Cilium for enforcement. Do not present local evidence as an AKS observation.

## One-command proof

```bash
make kind-demo
```

The target builds the backend image for the host architecture, creates the pinned cluster, installs
metrics-server, deploys Postgres/API/worker, runs probes, drives CPU load, deletes a worker during
persisted work, publishes aggregate evidence, and tears the cluster down even on failure.

For diagnosis, run the same stages explicitly:

```bash
make k8s-tools-check
make kind-image
make kind-up
make kind-load
make kind-deploy
make kind-smoke
make kind-hpa-demo
make kind-down
```

## Acceptance and evidence

The proof fails unless it observes the configured minimum, reaches the configured maximum, returns
to the minimum, deletes a worker, completes every submitted durable run, records no terminal run
failure, and observes a retried attempt. Revalidate the published artifact without a cluster:

```bash
make hpa-evidence-validate
```

Release 0.3 evidence is published at
[`k8s-hpa-scaling.md`](../reference/benchmarks/k8s-hpa-scaling.md). The current measured result is
1 → 5 → 1 API replicas and 100/100 durable runs after worker deletion. The JSON contains aggregate
timings, replica/CPU samples, workload resources, and cluster facts—no transaction bodies, secrets,
or local host paths.

## Cleanup verification

`make kind-down` deletes the configured cluster and verifies that its backing Docker containers no
longer exist. If a prior run was interrupted, rerun that target before starting again. Because kind
is local and disposable, there is no experiment-ledger row and no cloud teardown approval.
