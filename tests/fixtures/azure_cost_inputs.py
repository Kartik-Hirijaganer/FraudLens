"""Shared inputs for the Azure cost-model suites: paths, frozen prices, and an isolated repo root.

The generator reads committed Terraform and config from a repository root, so the failure-path
tests need a writable copy of exactly those sources. Staging them here keeps the model suite and
the CLI suite pointing at one definition instead of two that can drift.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_PRICES = REPO_ROOT / "tests" / "fixtures" / "azure_retail_prices_frozen.json"
GENERATED_DOC = REPO_ROOT / "docs" / "reference" / "cost-model.md"
PINNED_DATE = "2026-09-15"

STAGED_SOURCES = (
    "config/cost-model.yaml",
    "config/experiments/budget.yaml",
    "infra/terraform/environments/prod/prod.tfvars",
    "infra/terraform/modules/gateway_app/main.tf",
    "infra/terraform/modules/observability/main.tf",
    "infra/terraform/environments/aks-demo/aks-demo.tfvars",
)


def stage_repo(tmp_path: Path) -> Path:
    """Copy every committed source the generator reads into an isolated repo root."""
    root = tmp_path / "repo"
    for relative in STAGED_SOURCES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((REPO_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    return root
