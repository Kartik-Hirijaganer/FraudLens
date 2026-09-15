"""Summary: Operator CLI behind `make azure-cost-plan`. It reads every deployment shape from the
committed Terraform sources, resolves every unit rate live from the public Azure Retail Prices
API (read-only and unauthenticated — it creates nothing, so Golden Rule 7 does not apply),
projects the recurring Container Apps cost and the per-session ephemeral AKS cost, writes
`docs/reference/cost-model.md`, and exits non-zero if either enforced ceiling is breached.

Key classes:
- (none)

Key functions:
- main: resolve prices, build the projection, write the document, and gate on the ceilings.

Notes:
- `--prices-file` replays a frozen price set so a run is fully deterministic; `--save-prices`
  captures the live response as a reusable fixture. The unit tests use the former.
- `--date` pins the generation date so a regenerated document diffs cleanly in tests.
- The document is written even when a ceiling fails, so the reader can see why.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from lib.azure_cost.config import (
    DEFAULT_COST_MODEL_CONFIG,
    REPO_ROOT,
    CostModelConfigError,
    load_cost_model_config,
)
from lib.azure_cost.model import build_cost_model
from lib.azure_cost.prices import (
    PriceCatalog,
    PriceError,
    dump_price_items,
    fetch_price_items,
    load_price_items,
)
from lib.azure_cost.report import render_cost_model
from lib.azure_cost.shapes import ShapeError, load_shapes

DEFAULT_OUTPUT = Path("docs/reference/cost-model.md")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project FraudLens Azure cost from committed shapes and live retail prices."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_COST_MODEL_CONFIG)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--prices-file", type=Path, default=None, help="Replay frozen retail price rows."
    )
    parser.add_argument(
        "--save-prices", type=Path, default=None, help="Write fetched rows as a frozen fixture."
    )
    parser.add_argument("--date", default=None, help="Override the generation date (YYYY-MM-DD).")
    parser.add_argument(
        "--print-only", action="store_true", help="Do not write the generated document."
    )
    return parser


def _display(destination: Path, repo_root: Path) -> str:
    """Return the repository-relative path when the output lives inside the repo."""
    try:
        return str(destination.relative_to(repo_root))
    except ValueError:
        return str(destination)


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve prices, build the projection, write the document, and gate on the ceilings."""
    args = _build_parser().parse_args(argv)
    repo_root: Path = args.repo_root
    try:
        config = load_cost_model_config(repo_root, args.config)
        shapes = load_shapes(config, repo_root)
        if args.prices_file is not None:
            items = load_price_items(args.prices_file)
        else:
            items = fetch_price_items(
                config,
                {"aca": shapes.aca_region, "aks": shapes.aks_region},
                {
                    "aks_system_vm_size": shapes.aks_system_vm_size,
                    "aks_user_vm_size": shapes.aks_user_vm_size,
                },
            )
            if args.save_prices is not None:
                dump_price_items(items, args.save_prices)
        generated_on = args.date or datetime.now(UTC).date().isoformat()
        model = build_cost_model(config, shapes, PriceCatalog(items), generated_on, repo_root)
    except (CostModelConfigError, ShapeError, PriceError) as error:
        print(f"azure-cost-plan failed: {error}")
        return 2
    document = render_cost_model(model)
    if not args.print_only:
        destination = args.output if args.output.is_absolute() else repo_root / args.output
        destination.write_text(document, encoding="utf-8")
        print(f">> wrote {_display(destination, repo_root)} ({generated_on})")
    print(
        f">> recurring ${model.fixed_monthly_usd}/month; "
        f"AKS ${model.aks.session_usd}/session "
        f"(${model.aks.cost_with_margin_usd} with margin, ceiling ${model.aks.ceiling_usd})"
    )
    for failure in model.failures:
        print(f"ceiling breached: {failure}")
    if model.failures:
        return 1
    print(">> azure-cost-plan OK: every enforced ceiling holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
