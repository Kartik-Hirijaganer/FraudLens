# ADR-028 — Paid experiments use admission gates and verified teardown

- **Status:** Accepted
- **Date:** 2026-09-14

## Context

Release 0.3 needs bounded CPU training and GPU inference experiments, but cloud list price is not a
reliable spending control. Runtime uncertainty, quota failures, stopped-resource storage charges,
Spot eviction, and delayed billing can all exceed a casual estimate. Evidence must also identify
which provider, SKU, protocol, and source data produced a claim.

### Options considered and rejected

1. **Rely on cloud budget alerts** — rejected because alerts lag and do not stop resources.
2. **Approve an entire release once** — rejected because creation, full-run admission, and deletion
   have different evidence and risk.
3. **Use Spot unconditionally** — rejected because capacity and eviction can invalidate equal-host
   comparisons or strand partial artifacts.
4. **Treat stopped compute as teardown** — rejected because persistent disks, network volumes,
   public IPs, storage, and budgets may remain billable.
5. **Publish estimates as measurements** — rejected because release claims must link to observed,
   hash-bound artifacts.

## Decision

Every paid experiment is one named resource session. Before creation it must have a current-plan
ledger row, an allocation and watchdog in
[`config/experiments/budget.yaml`](../../../config/experiments/budget.yaml), a current provider
quote, and an explicit human approval. A smoke or pilot measures the real path. The full run is
admitted only when measured runtime projected to the frozen workload, plus the configured 30%
margin and current unsettled sessions, fits both its allocation and the one-time $75 ceiling.

Cloud mutations are split into explicit create, upload/sync, start, stop, export, and delete gates.
Provider-native auto-shutdown or stop is a backstop, not teardown: disks, volumes, IPs, budgets, and
storage may continue billing. After local artifact validation, deletion requires approval and a
read-only provider query must prove that no scoped billable resource remains. The ledger records
start/stop timestamps, quoted rate, projection, settled cost when available, run ID, and teardown
status. Publication fails when its run is not represented in the ledger.

RunPod Secure Cloud RTX 4090 is the default GPU path. Azure A100/A10 remains opportunistic only when
fresh quota and price checks pass the same admission gate. Provider choice does not change the
frozen BF16-versus-AWQ protocol or allow the two arms to use different hardware.

### Evidence

- [`ledger.md`](../../reference/experiments/ledger.md) is the auditable session register.
- [`budget.yaml`](../../../config/experiments/budget.yaml) contains allocations, watchdog bounds,
  and dated price provenance.
- [`data-batch.md`](../../runbooks/data-batch.md) and
  [`vllm-benchmark.md`](../../runbooks/vllm-benchmark.md) define the pilot and teardown gates.
- `make experiment-budget-check` validates ceiling reconciliation and publication lineage.

## Tradeoffs accepted

- Multiple human gates slow an experiment, but bound cost and make each irreversible decision
  reviewable.
- A 30% margin can reject work that might have fit. The reserve is preferable to an unplanned
  overrun and can be changed only through an explicit budget decision.
- Billing settlement may trail teardown. The ledger conservatively counts projected cost until the
  actual value arrives.
- Provider-neutral allocations permit RunPod or Azure, while provider-specific rates and hardware
  remain recorded so portability cannot erase provenance.

## Reconsider when

- A sandbox account enforces hard spending caps and automatic resource expiry across every resource
  type.
- Repeated runs establish narrow runtime variance that justifies a different admission margin.
- The project gains recurring production workloads; those require operational budgets and SLOs,
  not this one-time experiment protocol.

No estimate, quota approval, stopped VM, or stopped pod is evidence of successful execution or clean
teardown. Both require their own observed record.
