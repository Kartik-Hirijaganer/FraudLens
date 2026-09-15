"""Summary: Mechanical Markdown and Mermaid benchmark rendering tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Render assertions cover stable structure without snapshotting timestamps.
"""

from __future__ import annotations

from vllm_bench_fakes import complete_benchmark

from lib.vllm_bench.config import load_config
from lib.vllm_bench.render import render_markdown
from lib.vllm_bench.report import build_report


def test_markdown_contains_derived_acceptance_chart_quality_and_provenance() -> None:
    config, artifact, manifest = complete_benchmark(load_config())
    rendered = render_markdown(build_report(manifest, artifact, config))
    assert rendered.startswith("# vLLM BF16 versus 4-bit AWQ SAR benchmark\n")
    assert "## Acceptance" in rendered
    assert "| profile_full | full | full | PASS |" in rendered
    assert "```mermaid\nxychart-beta" in rendered
    assert 'title "p95 latency by concurrency"' in rendered
    assert "| BF16 | 1 |" in rendered
    assert "| AWQ | 2 |" in rendered
    assert "## Memory and quality" in rendered
    assert "### BF16" in rendered and "### AWQ" in rendered
    assert manifest.servers["bf16"].image_digest in rendered
    assert rendered.endswith("\n")
