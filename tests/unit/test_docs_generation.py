"""Summary: Deterministic OpenAPI YAML and standalone Scalar generation tests.

Key classes:
- (none)

Key functions:
- test_openapi_yaml_round_trips_live_schema: prove YAML and live JSON have equal meaning.
- test_standalone_scalar_embeds_schema: prove the generated page does not fetch the schema.
- test_k8s_benchmark_reports_only_platforms_that_published: prove an unrun platform adds no row.

Notes:
- Browser assets remain configuration-owned; only the OpenAPI document is embedded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from lib.docs_benchmarks import (
    render_fulldata_training,
    render_k8s_benchmark,
    render_make_targets,
    render_vllm_benchmark,
)
from update_docs import REPO_ROOT, _load_app, _openapi_yaml, _standalone_scalar_html


def test_openapi_yaml_round_trips_live_schema() -> None:
    """The generated YAML must preserve the live OpenAPI schema exactly."""
    app = _load_app()

    assert yaml.safe_load(_openapi_yaml(app)) == app.openapi()


def test_standalone_scalar_embeds_schema() -> None:
    """The generated Scalar page must embed the schema instead of fetching /openapi.json."""
    html = _standalone_scalar_html(_load_app())

    assert "Scalar.createApiReference" in html
    assert '"content": {"openapi": "3.1.0"' in html
    assert '"url": "/openapi.json"' not in html
    assert '"telemetry": false' in html
    assert all(line == line.rstrip() for line in html.splitlines())
    assert html.endswith("\n") and not html.endswith("\n\n")


def test_readme_regions_use_evidence_and_makefile() -> None:
    """README renderers must expose the committed measured benchmark evidence."""
    vllm = render_vllm_benchmark(REPO_ROOT)
    assert "63.5%" in vllm
    assert "Acceptance |" in vllm
    assert "not met" in vllm
    assert "60.8% at concurrency 32" in vllm
    assert "hi-medium" in render_fulldata_training(REPO_ROOT)
    assert "0.3196" in render_fulldata_training(REPO_ROOT)
    k8s = render_k8s_benchmark(REPO_ROOT)
    assert "1 → 5 → 1" in k8s
    assert "| kind |" in k8s and "| aks |" in k8s
    assert "| aks | 1 → 5 → 1 | 101 s | 117 s | 100/100 | 0 |" in k8s
    assert "Complete local PR preflight" in render_make_targets(REPO_ROOT)


def test_k8s_benchmark_reports_only_platforms_that_published(tmp_path: Path) -> None:
    """An unrun platform must contribute no row, and no run at all must stay explicitly pending."""
    benchmarks = tmp_path / "docs/reference/benchmarks"
    benchmarks.mkdir(parents=True)

    assert "Pending" in render_k8s_benchmark(tmp_path)
    assert "| aks |" not in render_k8s_benchmark(tmp_path)

    shutil.copy(
        REPO_ROOT / "docs/reference/benchmarks/k8s-hpa-scaling.json",
        benchmarks / "k8s-hpa-scaling.json",
    )
    kind_only = render_k8s_benchmark(tmp_path)

    assert "| kind |" in kind_only
    assert "| aks |" not in kind_only
    assert "Pending" not in kind_only
