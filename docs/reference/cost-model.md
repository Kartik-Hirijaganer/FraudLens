# Azure Cost Model (generated)

> **Generated - do not edit by hand.** Regenerate with `make azure-cost-plan`, which
> reads every deployment shape from the committed Terraform sources and every unit rate
> live from the public Azure Retail Prices API. Editing this file by hand detaches the
> numbers from the configuration they describe.

- **Generated on:** 2026-09-16
- **Currency:** USD
- **Recurring monthly total:** **$11.53**
- **Per ephemeral AKS session:** **$0.79** ($1.03 with the ADR-028 0.3 margin)

## Enforced ceilings

These are gates, not guidance: `make azure-cost-plan` exits non-zero when either fails.

| Ceiling | Value | Observed | Verdict |
| --- | --- | --- | --- |
| Container Apps maximum replicas | 1 | 1 | PASS |
| AKS cost per session (with margin) | $5.00 | $1.03 | PASS |

## Committed shapes priced

Each value below is read from the Terraform source that owns it, so a configuration
change moves this projection with no second copy to maintain.

| Shape | Value |
| --- | --- |
| Container Apps region | eastus2 |
| Container Apps replicas (min / max) | 1 / 1 |
| Container Apps vCPU / memory per replica | 0.5 vCPU / 1 GiB |
| Log Analytics daily ingestion cap | 0.1 GB/day |
| AKS region | westus3 |
| AKS system node SKU | Standard_B2s |
| AKS user node SKU | Standard_D2as_v4 |
| AKS user nodes (min / max) | 1 / 2 |

## Container Apps — the permanent URL (recurring)

Priced in `eastus2` on the Consumption plan. The free monthly grant
covers **100 warm replica-hours** at this
shape; the keep-warm window holds **720 replica-hours/month** warm, so
**620 hours** are billed — at the *idle*
rate, because a replica that exists but is not serving bills eight times
cheaper than one that is.

| Item | Basis | Cost/month |
| --- | --- | --- |
| Compute — 620 warm replica-hours beyond the free grant, idle rate | 720 warm h/mo (24 h x 30 days) - 100 free h x 3600 s x $0.0000045/replica-second | $10.04 |
| Requests | 20000 requests/mo - 2000000 free @ $0.4/1M | $0.00 |
| Log Analytics ingestion — expected | 0.5 GB/mo x $2.76/GB (capped at 0.1 GB/day => max $8.28/mo) | $1.38 |
| Blob storage — artifacts and SAR PDFs | 5 GB hot LRS x $0.0184/GB-month | $0.09 |
| Blob storage — Terraform remote state | 1 GB hot LRS x $0.0184/GB-month | $0.02 |
| **Total** | | **$11.53** |

| Scenario | Monthly |
| --- | --- |
| As configured (keep-warm window, idle rate) | $11.53 |
| Every hour billed at the *active* rate, at the 1-replica cap, log ingestion pinned to its daily cap | $42.41 |
| Log ingestion alone, pinned to the 0.1 GB/day cap | $8.28 |

The second row is the bound the hard caps enforce: `max_replicas` cannot be exceeded,
and the workspace stops ingesting at its daily quota rather than billing on.

## Cold start — what the keep-warm cron is buying

`min_replicas = 0` is the single largest saving on the recurring bill, and its only
cost is the first request after an idle period. The keep-warm cron hides that request
and is worth its own line above only while a cold start exceeds **5 s**.

| Measurement | Seconds |
| --- | --- |
| First request after idle (cold) | 111.798648 |
| Request immediately after (warm) | 0.058612 |
| Measured on | 2026-09-16 |

Verdict at the 5 s threshold: **keep-warm earns its cost**.

> Force scale-to-zero by idling past the cooldown, then time two sequential requests: `curl -o /dev/null -s -w '%{time_total}' https://<app_fqdn>/healthz` — the first cold, the second immediately after it.

## AKS — one governed ephemeral session (4 hours)

Priced in `westus3`. The cluster is created and destroyed per session, so a
month with no session costs nothing.

| Item | Basis | Cost |
| --- | --- | --- |
| AKS control plane — Free tier | sku_tier = Free: the managed control plane is not billed | $0.00 |
| 1 x Standard_B2s system node | 4 h x $0.0416/h | $0.17 |
| 1 x Standard_D2as_v4 user node (baseline) | 1 x 4 h x $0.096/h | $0.38 |
| 1 x Standard_D2as_v4 user node during HPA scale-out | 1 x 1 h x $0.096/h | $0.10 |
| 1 x Standard Load Balancer | 1 x 4 h x $0.025/h | $0.10 |
| 2 x Standard static public IP | 2 x 4 h x $0.005/h | $0.04 |
| AKS Log Analytics | monitoring_enabled = false: no workspace is created for the session | $0.00 |
| **Total per session** | | **$0.79** |

Admission (ADR-028): $0.79 x (1 + 0.3) = **$1.03** against a **$5.00** ceiling — PASS.

## Unit rates

Every rate below was resolved at generation time. A rate the retail API does not
expose is marked *(list)* and carries its published source instead.

| Meter | Region | Unit price | Unit | Effective from | Source |
| --- | --- | --- | --- | --- | --- |
| Container Apps Standard Memory Active | eastus2 | $0.000003 | 1 GiB Second | 2022-06-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Container Apps Standard Memory Idle | eastus2 | $0.000003 | 1 GiB Second | 2022-06-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Container Apps Standard Requests | eastus2 | $0.400000 | 1M | 2022-06-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Container Apps Standard vCPU Active | eastus2 | $0.000024 | 1 Second | 2022-06-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Container Apps Standard vCPU Idle | eastus2 | $0.000003 | 1 Second | 2022-06-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| AKS system node pool VM | westus3 | $0.041600 | 1 Hour | 2025-10-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| AKS user node pool VM | westus3 | $0.096000 | 1 Hour | 2022-12-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Blob Storage Hot LRS Data Stored | eastus2 | $0.018400 | 1 GB/Month | 2017-02-03T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Log Analytics Analytics Logs Data Ingestion | eastus2 | $2.760000 | 1 GB | 2018-02-01T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |
| Standard Load Balancer, first 5 rules *(list)* | westus3 | $0.025000 | 1 Hour | — | [source](https://azure.microsoft.com/en-us/pricing/details/load-balancer/) |
| Standard IPv4 Static Public IP | westus3 | $0.005000 | 1 Hour | 2021-04-20T00:00:00Z | [source](https://prices.azure.com/api/retail/prices) |

## Not priced by this model

| Service | Why the gap is acceptable |
| --- | --- |
| Azure Container Registry | acr_enabled = false; backend images are pulled from public GHCR at no cost. |
| Container Apps custom-network Load Balancer and public IP | Removed by D1; a platform-managed environment provisions neither. |
| Azure bandwidth and egress | Synthetic-data traffic is far below the 100 GB/month free allowance. |
| AKS Log Analytics | monitoring_enabled = false on the ephemeral demonstration cluster. |
| AKS node OS disks | Both pools commit os_disk_type = "Managed" at 64 GB, not the ephemeral disk both SKUs support, so each node bills a P6 Premium SSD instead of nothing. Left unpriced rather than assumed to be zero: it cannot move the session past its ceiling. Going ephemeral needs os_disk_size_gb inside each SKU's cache - Standard_B2s 30 GiB, Standard_D2as_v4 50 GiB - which Terraform cannot validate before apply. |
| Supabase Postgres | Not an Azure meter; the project runs on the free tier (ADR-011). |
| Vercel frontend hosting | Not an Azure meter; the SPA is served from the free Hobby tier. |
| Infisical secret management | Not an Azure meter; secrets resolve from the free tier (ADR-010). |
| OpenRouter LLM spend | Not an Azure meter; capped separately by llm_daily_budget_usd in config/prod.yaml (D4). |
| GitHub Actions minutes | Free for public repositories, including the keep-warm and watchdog crons. |
| Azure Automation budget-triggered shutdown | Explicitly out of core scope; the hard caps already bound the bill. |
