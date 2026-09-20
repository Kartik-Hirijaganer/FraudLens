# Measured evidence — generated summary tables

Every table below is written by `make docs` directly from the published benchmark artifacts in
this directory. **Do not hand-edit them:** `make docs-check` regenerates each one in memory and
fails CI if what is committed has drifted. Each table links onward to the full report, which
carries the run ids, evidence hashes, acceptance criteria, and caveats.

## Inference benchmark — vLLM BF16 versus 4-bit AWQ

<!-- AUTOGEN:vllm-benchmark -->
| Cases | BF16 weight memory | AWQ weight memory | Reduction | Acceptance |
| ---: | ---: | ---: | ---: | --- |
| 1000 | 14.25 GiB | 5.20 GiB | 63.5% | not met |

Acceptance NOT met (reference_validity). AWQ reduced parsed model-weight memory by 63.5%; AWQ throughput higher by 60.8% at concurrency 32.
<!-- /AUTOGEN:vllm-benchmark -->

Full report: [`vllm-awq-sar-benchmark.md`](vllm-awq-sar-benchmark.md).

## Quality-gated SAR cascade

<!-- AUTOGEN:cascade-benchmark -->
| Scenario | Cases served | Citation fabrications | Escalated | Case p95 | GPU-hours / case | Endpoints |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bf16-baseline | 99.5% | 0 | 0.0% | 16,164 ms | 0.000116 | 1 |
| awq-raw | 90.9% | 85 | 0.0% | 16,228 ms | 0.000113 | 1 |
| awq-bf16-unconstrained | 99.7% | 0 | 9.2% | 17,184 ms | 0.000204 | 2 |

Gated awq-bf16-unconstrained served 99.7% of 1000 cases with 9.2% escalated; case p95 latency 6.3% higher than bf16-baseline at concurrency 32 across two endpoints, not one; AWQ model weights 63.5% smaller.
<!-- /AUTOGEN:cascade-benchmark -->

The cascade's p95 and GPU-time are measured across **two** simultaneously provisioned endpoints
against a one-endpoint baseline — an architecture comparison, not a same-hardware one. Full
report: [`vllm-gated-cascade-benchmark.md`](vllm-gated-cascade-benchmark.md).

## Training at scale — 68.2M IBM transactions

<!-- AUTOGEN:fulldata-training -->
| Candidate | Source rows | Usable rows | Training rows | Holdout rows | PR-AUC | Gates |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| hi-small | 5078345 | 5054380 | 3032849 | 1010853 | 0.2632 | failed |
| hi-medium | 31898238 | 31796234 | 19078362 | 6358506 | 0.3196 | passed |
| li-medium | 31251483 | 31157276 | 18695899 | 6231386 | 0.0839 | failed |
<!-- /AUTOGEN:fulldata-training -->

Source rows are not model-fitting rows: usability rules, whole-account temporal folds,
calibration, and holdout evaluation reduce the training population. Full report:
[`ibm-full-data-training.md`](ibm-full-data-training.md).

## Kubernetes autoscaling — kind and AKS

<!-- AUTOGEN:k8s-benchmark -->
| Platform | API replicas | First scale-up | Scale-back | Durable runs | Failed runs |
| --- | --- | ---: | ---: | ---: | ---: |
| kind | 1 → 5 → 1 | 46 s | 92 s | 100/100 | 0 |
| aks | 1 → 5 → 1 | 101 s | 117 s | 100/100 | 0 |
<!-- /AUTOGEN:k8s-benchmark -->

AKS scale-out is slower than kind because pods bind on node capacity and trigger the cluster
autoscaler — node autoscaling stacked on pod autoscaling, not a regression. The AKS load drove
genuine scale-out, but the rate limiter rejected almost all of it: **1,147 of 783,498 health
requests succeeded (0.15%)**. These rows are an autoscaling result and are **not** a
sustained-throughput result. Full reports: [`k8s-hpa-scaling.md`](k8s-hpa-scaling.md) (kind) and
[`aks-hpa-scaling.md`](aks-hpa-scaling.md) (AKS).
