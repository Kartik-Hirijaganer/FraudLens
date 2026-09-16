# ADR-029 — The permanent deployment runs under a recurring operational budget

- **Status:** Accepted
- **Date:** 2026-09-16
- **Format:** Decision · Options · Why · Tradeoffs · Reconsider when
- **Related:** implementation plan
  [`plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md`](../../../plans/2026-09-15-azure-deployment-cost-projection-and-budget-alerts.md)

## Context

Release 0.4.0 gives FraudLens a permanent, recruiter-facing URL. That is a different cost shape from
anything the repository has governed so far. [ADR-028](ADR-028-paid-experiment-governance.md) covers
**one-time paid experiments** against a $75 ceiling: each one is a named session with a pilot
projection, an allocation, and a verified teardown, and its spend stops when the resources are
destroyed. A permanent URL never gets torn down, has no pilot to measure, and cannot be reconciled
by a teardown query. ADR-028's own reconsideration clause routes exactly this case elsewhere: *"The
project gains recurring production workloads; those require operational budgets and SLOs, not this
one-time experiment protocol."* This record is that instrument.

Two facts make it necessary rather than ceremonial. The subscription is Pay-As-You-Go with the
**spending limit off**, so no hard stop exists at the account level. And Azure budgets *alert*; they
never halt a resource, and their data lags by hours — long enough for a misconfiguration to run
unobserved. Alerts therefore cannot be the control. They are the notification that a control was
approached.

## Decision

The permanent deployment runs under a standing operational budget of **$25/month of Azure spend**,
against a generated projection of **$2.72/month**. The ceiling is enforced by limits that cannot be
exceeded; the budgets tell the owner when something unexpected is happening. Both numbers and every
unit rate behind them live in the generated [cost model](../../reference/cost-model.md), which
`make azure-cost-plan` rebuilds from the committed Terraform shapes and the live Azure Retail Prices
API — this record owns the policy, not a second copy of the arithmetic.

**Two budget scopes, both applied and both monthly.** They notify at 50%, 80%, and 100% actual, and
100% forecast, to the human owner's address:

| Budget | Scope | Amount | Root |
| --- | --- | ---: | --- |
| `fraudlens-prod-budget` | the `prod` resource group | $25 | [`environments/prod`](../../../infra/terraform/environments/prod/) |
| `fraudlens-cost-guardrails-budget` | subscription-wide, **no dimension filter** | $25 | [`environments/cost-guardrails`](../../../infra/terraform/environments/cost-guardrails/) |

The RG-scoped budget makes prod spend attributable. The unfiltered one is the only instrument that
sees a resource created outside a named group — by hand, by a mistyped root, or by a service that
provisions its own. Budgets are free, so the second scope costs nothing but catches what the first
cannot. The AKS session budget stays separate and ephemeral: it is created and destroyed with the
cluster under ADR-028, not carried here.

**Hard caps are the control.** Each is a ceiling a runaway cannot pass, not a threshold it crosses:

| Cap | Value | Where it is enforced |
| --- | --- | --- |
| Container Apps maximum replicas | **1** | `max_replicas` in [`prod.tfvars`](../../../infra/terraform/environments/prod/prod.tfvars) |
| Log Analytics ingestion | **0.1 GB/day** | `daily_quota_gb` in [`modules/observability`](../../../infra/terraform/modules/observability/) — the workspace stops ingesting rather than billing on |
| OpenRouter LLM spend | **$0.25/day** | `llm_daily_budget_usd` in [`config/prod.yaml`](../../../config/prod.yaml), fail-closed in the investigation path |
| Scheduled Container Apps Jobs | **none — manual trigger only** | [`environments/prod/main.tf`](../../../infra/terraform/environments/prod/main.tf) |
| AKS session lifetime | **4-hour wall clock, teardown under `if: always()`** | [`deploy-aks.yml`](../../../.github/workflows/deploy-aks.yml) |
| AKS session cost | **$5.00 admission ceiling** | `make azure-cost-plan`, which exits non-zero above it |

The LLM ceiling is recorded here although OpenRouter is not an Azure meter: it is recurring
operational spend on the same public URL, and the $25 Azure budget would never see it.

**The keep-warm window is a priced decision, not a habit.** The app runs at `min_replicas = 0`, which
is the single largest saving on the recurring bill and costs one cold start per idle period — a
**measured 111.8 s**, against 0.059 s warm. [`keep-warm.yml`](../../../.github/workflows/keep-warm.yml)
pings `/healthz` every 4 minutes on weekdays 13:00–21:00 UTC (09:00–17:00 ET): 8 h × 22 weekdays =
176 warm replica-hours/month, of which the free grant covers 100 and the remaining 76 bill at the
Container Apps *idle* rate — roughly an eighth of the active rate — for **$1.23/month**, against
~$10.20 for a paid `min_replicas = 1` floor. The window holds the hours a visitor would plausibly
click and nothing more. It is worth its line only while the cold start exceeds **5 s**; below that
threshold `KEEP_WARM_ENABLED` is unset and the compute line leaves the recurring total.

**A daily watchdog closes the lag.** [`cost-watchdog.yml`](../../../.github/workflows/cost-watchdog.yml)
runs at 13:00 UTC with **read-only `az` only** — group existence, cluster list, tagged-resource list,
budget list, and a month-to-date Cost Management query — and fails the run, reaching the owner
through ordinary Actions notifications, when an AKS resource group survives or month-to-date cost
passes its threshold. A forgotten teardown surfaces within 24 hours instead of whenever a budget
threshold happens to trip, which on a small budget could be a week of burn. Being read-only, it
creates nothing and needs no Golden Rule 7 gate.

**Review cadence: monthly, against the settled bill.** Once each billing month closes, compare actual
Azure cost to the generated projection, regenerate the cost model with `make azure-cost-plan`, and
commit the diff. Any deployment shape or unit rate that moved shows up as a changed number under
review rather than as a surprise on a bill. A projection that drifts from settled cost by more than
the 30% ADR-028 margin is a defect in the model, and is fixed there rather than absorbed.

**The permanent link runs on Container Apps; AKS stays ephemeral.** A cluster bills for its nodes
whether or not anyone visits — about **$0.17/hour**, roughly **$126/month** if left standing, against
the **$2.72/month** recurring total above: **~50×** the cost for the same visible result, since both
serve the same image and the same API. So Container Apps carries the URL, and AKS is created for a
governed session, measured, and destroyed. What persists at $0 is the Terraform, the committed
evidence artifact, and the workflow logs — which is what is actually shown to anyone. The claim that
rests on it stays literally true and is worded in
[ADR-021](ADR-021-aks-ephemeral-kubernetes-demonstration.md)'s amendment.

## Evidence

- [`cost-model.md`](../../reference/cost-model.md) is the generated projection: enforced ceilings and
  their observed values, priced shapes, dated unit rates with source URLs, the measured cold start,
  and an explicit list of what is *not* priced.
- [`azure-deploy.md`](../../runbooks/azure-deploy.md) is the operating procedure for the permanent
  URL; [`aks-deploy.md`](../../runbooks/aks-deploy.md) is the bounded AKS session.
- [`environments/cost-guardrails`](../../../infra/terraform/environments/cost-guardrails/) creates
  exactly one resource — an unfiltered subscription budget.
- [`claims.md`](../../reference/claims.md) records the cost-control claim at the level these
  artifacts support.
- `make azure-cost-plan` is a gate, not a report: it fails the build when the Container Apps replica
  cap or the AKS session ceiling is breached, so a shape change cannot quietly raise the bill.

## Options considered and rejected

1. **Extend ADR-028's $75 experiment ceiling to cover the URL** — rejected because that ceiling is
   one-time and reconciled by teardown. A recurring charge would consume it monotonically and leave
   no allocation for the experiments the instrument exists to govern.
2. **Rely on the Azure budgets alone** — rejected because budgets never stop a resource and their
   data lags by hours; with the subscription spending limit off, nothing below the caps would bind.
3. **Turn the subscription spending limit on** — rejected because it is unavailable on this
   Pay-As-You-Go offer, and its all-or-nothing suspension would take the live URL down rather than
   bound it.
4. **Keep AKS running to serve the permanent URL** — rejected on the ~50× ratio above for no visible
   difference to a reader, and because a standing cluster removes the verified-teardown discipline
   that makes the paid session auditable.
5. **Pay for `min_replicas = 1` instead of the keep-warm cron** — rejected at ~$10.20/month versus
   $1.23 for an outcome a visitor cannot distinguish inside the window that matters.
6. **Automated shutdown at 80% of budget** (an Automation account with a narrow custom role, driven
   by the budget action group) — rejected as core scope: the hard caps already bound the bill, and it
   would add a custom RBAC role and a runbook to maintain. It remains fully specified in the plan if
   the caps ever stop being sufficient.

## Tradeoffs accepted

- A $25 budget against a $2.72 projection is deliberately loose. Tight alerting on a small bill
  produces noise that gets ignored; the caps, not the alert, are what bind the maximum.
- Scale-to-zero trades a cold start for most of the recurring saving. Outside the keep-warm window a
  visitor waits, and that is accepted rather than paid away.
- GitHub scheduled triggers are best-effort and can be delayed under load. A missed keep-warm ping
  costs one cold start; a delayed watchdog costs detection latency. Neither costs money.
- The watchdog is read-only by design, so it reports a surviving resource and never removes one.
  Teardown stays an explicitly approved human action under Golden Rule 7.
- The projection excludes named gaps — AKS node OS disks among them — rather than assuming they are
  zero. They are listed in the cost model so the gap is visible and cannot move a session past its
  ceiling unnoticed.

## Reconsider when

- Settled monthly cost exceeds the $25 ceiling, or diverges from the projection by more than the 30%
  ADR-028 margin for two consecutive months.
- Real users, an availability commitment, or an SLO enters scope — a keep-warm cron and a one-replica
  cap are a portfolio posture, not a production one.
- Real PHI or regulated data enters scope, which changes the compute, network, and retention shape
  before it changes the price.
- AKS becomes the application target rather than a demonstration runtime, at which point the ~50×
  ratio is a deliberate purchase and this budget is replaced, not stretched.
- The caps stop being sufficient — a new always-on service, a second region, or a paid dependency
  outside these meters — at which point the budget-triggered automated shutdown becomes worth its
  maintenance.

A budget alert is not a spending control, and a projection is not a bill. The caps bound the
maximum, the watchdog bounds the detection delay, and the settled monthly invoice is the only record
that closes the loop.
