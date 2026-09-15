"""Summary: Renders the cost projection into `docs/reference/cost-model.md`. The document is a
generated artifact: it carries its generation date, every unit rate with the URL it came from,
the committed shapes each figure was derived from, the enforced ceilings and their verdicts, and
a separate table of the services deliberately left unpriced so the gaps are visible rather than
implied. Output is deterministic for a fixed date and price set, so it diffs cleanly.

Key classes:
- (none)

Key functions:
- render_cost_model: return the complete Markdown document for a projection.
- cold_start_section: render the measured cold start, or the instructions for taking it.

Notes:
- Rates are shown at six decimal places because the Container Apps meters bill per second and
  round to nothing at two.
- A rate the retail API does not expose is marked `(list)` against its published source, matching
  the provenance convention `config/experiments/budget.yaml` already uses.
"""

from __future__ import annotations

from decimal import Decimal

from lib.azure_cost.config import ColdStart
from lib.azure_cost.model import CostLine, CostModel, plain_decimal

_MICRO = Decimal("0.000001")
_LIST_SOURCE = "published-list"


def _rows(lines: tuple[CostLine, ...]) -> list[str]:
    return [f"| {line.item} | {line.basis} | ${line.amount_usd} |" for line in lines]


def _rate_rows(model: CostModel) -> list[str]:
    rows: list[str] = []
    for rate in model.rates:
        marker = " *(list)*" if rate.source == _LIST_SOURCE else ""
        effective = rate.effective_from or "—"
        rows.append(
            f"| {rate.label}{marker} | {rate.region} | ${rate.price_usd.quantize(_MICRO)} | "
            f"{rate.unit_of_measure} | {effective} | [source]({rate.source_url}) |"
        )
    return rows


def _verdict(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def cold_start_section(cold_start: ColdStart) -> list[str]:
    """Render the measured cold start, or state plainly that it has not been measured yet."""
    threshold = plain_decimal(cold_start.keep_warm_threshold_seconds)
    lines = [
        "## Cold start — what the keep-warm cron is buying",
        "",
        "`min_replicas = 0` is the single largest saving on the recurring bill, and its only",
        "cost is the first request after an idle period. The keep-warm cron hides that request",
        f"and is worth its own line above only while a cold start exceeds **{threshold} s**.",
        "",
    ]
    if not cold_start.measured:
        lines.extend(
            [
                "**Not yet measured.** No figure is printed here, because the only honest",
                "alternative — deriving one from `cold_start_budget_seconds` — would be a",
                "configured allowance presented as evidence. Take the measurement against the",
                "deployed app and record it in `config/cost-model.yaml`:",
                "",
                f"> {cold_start.method}",
                "",
            ]
        )
        return lines
    cold = plain_decimal(cold_start.cold_seconds or Decimal("0"))
    warm = plain_decimal(cold_start.warm_seconds or Decimal("0"))
    keep_warm_earns_its_cost = (cold_start.cold_seconds or Decimal("0")) > (
        cold_start.keep_warm_threshold_seconds
    )
    verdict = "keep-warm earns its cost" if keep_warm_earns_its_cost else "disable keep-warm"
    lines.extend(
        [
            "| Measurement | Seconds |",
            "| --- | --- |",
            f"| First request after idle (cold) | {cold} |",
            f"| Request immediately after (warm) | {warm} |",
            f"| Measured on | {cold_start.measured_at} |",
            "",
            f"Verdict at the {threshold} s threshold: **{verdict}**.",
            "",
            f"> {cold_start.method}",
            "",
        ]
    )
    return lines


def render_cost_model(model: CostModel) -> str:
    """Return the complete generated cost-model document."""
    shapes = model.shapes
    aca, aks = model.aca, model.aks
    replica_ok = not any("max_replicas" in failure for failure in model.failures)
    lines = [
        "# Azure Cost Model (generated)",
        "",
        "> **Generated - do not edit by hand.** Regenerate with `make azure-cost-plan`, which",
        "> reads every deployment shape from the committed Terraform sources and every unit rate",
        "> live from the public Azure Retail Prices API. Editing this file by hand detaches the",
        "> numbers from the configuration they describe.",
        "",
        f"- **Generated on:** {model.generated_on}",
        "- **Currency:** USD",
        f"- **Recurring monthly total:** **${model.fixed_monthly_usd}**",
        f"- **Per ephemeral AKS session:** **${aks.session_usd}** "
        f"(${aks.cost_with_margin_usd} with the ADR-028 "
        f"{plain_decimal(aks.admission_margin)} margin)",
        "",
        "## Enforced ceilings",
        "",
        "These are gates, not guidance: `make azure-cost-plan` exits non-zero when either fails.",
        "",
        "| Ceiling | Value | Observed | Verdict |",
        "| --- | --- | --- | --- |",
        f"| Container Apps maximum replicas | 1 | {shapes.aca_max_replicas} | "
        f"{_verdict(replica_ok)} |",
        f"| AKS cost per session (with margin) | ${aks.ceiling_usd} | "
        f"${aks.cost_with_margin_usd} | {_verdict(aks.admitted)} |",
        "",
    ]
    if model.failures:
        lines.extend(["**Failures**", ""])
        lines.extend(f"- {failure}" for failure in model.failures)
        lines.append("")
    lines.extend(
        [
            "## Committed shapes priced",
            "",
            "Each value below is read from the Terraform source that owns it, so a configuration",
            "change moves this projection with no second copy to maintain.",
            "",
            "| Shape | Value |",
            "| --- | --- |",
            f"| Container Apps region | {shapes.aca_region} |",
            f"| Container Apps replicas (min / max) | {shapes.aca_min_replicas} / "
            f"{shapes.aca_max_replicas} |",
            f"| Container Apps vCPU / memory per replica | "
            f"{plain_decimal(shapes.aca_vcpu)} vCPU / "
            f"{plain_decimal(shapes.aca_memory_gib)} GiB |",
            f"| Log Analytics daily ingestion cap | "
            f"{plain_decimal(shapes.log_daily_quota_gb)} GB/day |",
            f"| AKS region | {shapes.aks_region} |",
            f"| AKS system node SKU | {shapes.aks_system_vm_size} |",
            f"| AKS user node SKU | {shapes.aks_user_vm_size} |",
            f"| AKS user nodes (min / max) | {shapes.aks_user_min_count} / "
            f"{shapes.aks_user_max_count} |",
            "",
            "## Container Apps — the permanent URL (recurring)",
            "",
            f"Priced in `{aca.region}` on the Consumption plan. The free monthly grant",
            f"covers **{plain_decimal(aca.free_grant_hours)} warm replica-hours** at this",
            "shape; the keep-warm window holds "
            f"**{plain_decimal(aca.warm_replica_hours)} replica-hours/month** warm, so",
            f"**{plain_decimal(aca.billable_hours)} hours** are billed — at the *idle*",
            "rate, because a replica that exists but is not serving bills eight times",
            "cheaper than one that is.",
            "",
            "| Item | Basis | Cost/month |",
            "| --- | --- | --- |",
            *_rows(aca.lines),
            f"| **Total** | | **${aca.monthly_usd}** |",
            "",
            "| Scenario | Monthly |",
            "| --- | --- |",
            f"| As configured (keep-warm window, idle rate) | ${aca.monthly_usd} |",
            f"| Every hour billed at the *active* rate, at the {shapes.aca_max_replicas}-replica "
            f"cap, log ingestion pinned to its daily cap | ${aca.active_rate_ceiling_usd} |",
            "| Log ingestion alone, pinned to the "
            f"{plain_decimal(shapes.log_daily_quota_gb)} GB/day cap | "
            f"${aca.log_ceiling_usd} |",
            "",
            "The second row is the bound the hard caps enforce: `max_replicas` cannot be exceeded,",
            "and the workspace stops ingesting at its daily quota rather than billing on.",
            "",
            *cold_start_section(model.cold_start),
            f"## AKS — one governed ephemeral session ({plain_decimal(aks.session_hours)} hours)",
            "",
            f"Priced in `{aks.region}`. The cluster is created and destroyed per session, so a",
            "month with no session costs nothing.",
            "",
            "| Item | Basis | Cost |",
            "| --- | --- | --- |",
            *_rows(aks.lines),
            f"| **Total per session** | | **${aks.session_usd}** |",
            "",
            f"Admission (ADR-028): ${aks.session_usd} x "
            f"(1 + {plain_decimal(aks.admission_margin)}) = "
            f"**${aks.cost_with_margin_usd}** against a **${aks.ceiling_usd}** ceiling — "
            f"{_verdict(aks.admitted)}.",
            "",
            "## Unit rates",
            "",
            "Every rate below was resolved at generation time. A rate the retail API does not",
            "expose is marked *(list)* and carries its published source instead.",
            "",
            "| Meter | Region | Unit price | Unit | Effective from | Source |",
            "| --- | --- | --- | --- | --- | --- |",
            *_rate_rows(model),
            "",
            "## Not priced by this model",
            "",
            "| Service | Why the gap is acceptable |",
            "| --- | --- |",
            *[f"| {item.service} | {item.reason} |" for item in model.excluded],
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
