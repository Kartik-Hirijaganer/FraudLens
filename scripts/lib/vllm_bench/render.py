"""Summary: Deterministic Markdown and Mermaid rendering for vLLM benchmark evidence.

Key classes:
- (none)

Key functions:
- render_markdown: render provenance, acceptance, performance, quality, cost, and charts.

Notes:
- All prose claims and chart values are derived from the validated typed report.
"""

from __future__ import annotations

from lib.vllm_bench.config import PurchaseOption
from lib.vllm_bench.metrics import LevelMetrics
from lib.vllm_bench.report_models import VllmBenchReport


def _cost(level: LevelMetrics, option: PurchaseOption) -> str:
    """Render one configured purchase-option projection or an explicit unavailable marker."""
    value = level.cost_per_1000_drafts_usd_by_purchase_option.get(option)
    return f"${value:.4f}" if value is not None else "n/a"


def _performance_rows(report: VllmBenchReport) -> list[str]:
    """Render one row for each arm and concurrency level."""
    rows = []
    for arm in report.arms:
        for level in arm.levels:
            rows.append(
                "| "
                + " | ".join(
                    (
                        arm.arm.upper(),
                        str(level.concurrency),
                        f"{level.latency_p50_ms:.1f}",
                        f"{level.latency_p95_ms:.1f}",
                        f"{level.latency_p99_ms:.1f}",
                        f"{level.ttft_p95_ms:.1f}",
                        f"{level.requests_per_second:.3f}",
                        f"{level.generated_tokens_per_second:.2f}",
                        f"{level.useful_drafts_per_second:.3f}",
                        f"${level.cost_per_1000_drafts_usd:.4f}",
                        _cost(level, "spot"),
                        _cost(level, "pay_as_you_go"),
                    )
                )
                + " |"
            )
    return rows


def render_markdown(report: VllmBenchReport) -> str:
    """Render the complete report with a Mermaid p95 comparison chart."""
    case_line = (
        f"- Cases: {report.measured_cases} measured + {report.abstention_cases} abstention fixtures"
    )
    acceptance_rows = [
        f"| {item.name} | {item.observed} | {item.required} | {'PASS' if item.passed else 'FAIL'} |"
        for item in report.acceptance
    ]
    delta_rows = [
        f"| {item.metric} | {item.delta_percentage_points:+.3f} | "
        f"{'YES' if item.warning else 'no'} |"
        for item in report.quality_deltas
    ]
    lines = [
        "# vLLM BF16 versus 4-bit AWQ SAR benchmark",
        "",
        f"> {report.headline}",
        "",
        f"- Run: `{report.run_id}`",
        f"- Profile: `{report.profile}`; case source: `{report.case_source}`",
        case_line,
        f"- Config SHA-256: `{report.config_sha256}`",
        f"- Cases SHA-256: `{report.cases_sha256}`",
        f"- Prompt: `{report.prompt_version}` (`{report.prompt_sha256}`)",
        f"- KV-cache comparison: `{report.kv_cache_mode}`",
        "",
        "## Acceptance",
        "",
        "| Criterion | Observed | Required | Result |",
        "|---|---:|---:|---|",
        *acceptance_rows,
        "",
        "## Performance",
        "",
        "| Arm | Concurrency | p50 ms | p95 ms | p99 ms | TTFT p95 ms | "
        "req/s | tok/s | useful/s | selected cost/1k | spot cost/1k | PAYG cost/1k |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *_performance_rows(report),
        "",
        "```mermaid",
        "xychart-beta",
        '    title "p95 latency by concurrency"',
        f"    x-axis [{', '.join(str(item.concurrency) for item in report.arms[0].levels)}]",
        '    y-axis "milliseconds" 0 --> '
        + f"{max(item.latency_p95_ms for arm in report.arms for item in arm.levels):.1f}",
        "    line ["
        + ", ".join(f"{item.latency_p95_ms:.1f}" for item in report.arms[0].levels)
        + "]",
        "    line ["
        + ", ".join(f"{item.latency_p95_ms:.1f}" for item in report.arms[1].levels)
        + "]",
        "```",
        "",
        "## Memory and quality",
        "",
        f"Parsed model-weight reduction: {report.weight_memory_reduction:.1%}.",
        f"Safetensors-size reduction (secondary): {report.safetensors_reduction:.1%}.",
        "",
        "| Metric | AWQ - BF16 (percentage points) | Warning |",
        "|---|---:|---|",
        *delta_rows,
        "",
        "## Provenance",
        "",
    ]
    for arm in report.arms:
        server = arm.server
        lines.extend(
            (
                f"### {arm.arm.upper()}",
                "",
                f"- Model: `{server.model}@{server.model_revision}`",
                f"- Tokenizer: `{server.tokenizer}@{server.tokenizer_revision}`",
                f"- Image: `{server.image}@{server.image_digest}`",
                f"- GPU/driver: `{server.gpu_name}` / `{server.driver_version}`",
                f"- Host: `{server.provider}` `{server.sku}` `{server.region}` "
                f"`{server.purchase_option}`",
                f"- Price: ${server.hourly_rate_usd:.6f}/h, verified "
                f"{server.price_verified_at} ({server.price_source_url})",
                f"- Weights/KV/max concurrency: {server.weight_memory_gib:.3f} GiB / "
                f"{server.kv_cache_tokens} tokens / {server.maximum_concurrency:.2f}x",
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"
