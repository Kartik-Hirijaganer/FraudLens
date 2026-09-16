"""Contracts for the `make azure-cost-plan` CLI and the document it generates.

The committed `docs/reference/cost-model.md` is an artifact, not prose: these cases prove it is
exactly what the generator produces from the frozen prices, that it states a breached ceiling
instead of hiding it, and that the CLI's exit code is the gate the Makefile relies on.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from azure_cost_inputs import (
    FROZEN_PRICES,
    GENERATED_DOC,
    PINNED_DATE,
    REPO_ROOT,
    stage_repo,
)

import azure_cost_plan
from lib.azure_cost.config import (
    CostModelConfig,
    CostModelConfigError,
    load_cost_model_config,
)
from lib.azure_cost.model import CostModel, build_cost_model, plain_decimal
from lib.azure_cost.prices import PriceCatalog, RetailPriceItem, load_price_items
from lib.azure_cost.report import render_cost_model
from lib.azure_cost.shapes import load_shapes, read_assignment


@pytest.fixture
def config() -> CostModelConfig:
    return load_cost_model_config(REPO_ROOT)


@pytest.fixture
def catalog() -> PriceCatalog:
    return PriceCatalog(load_price_items(FROZEN_PRICES))


def _model(config: CostModelConfig, catalog: PriceCatalog) -> CostModel:
    shapes = load_shapes(config, REPO_ROOT)
    return build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT)


def test_decimals_render_without_exponent_notation() -> None:
    # A Decimal 1.0E+2 in a cost document reads as a bug, not as 100 warm hours.
    assert plain_decimal(Decimal("1.0E+2")) == "100"
    assert plain_decimal(Decimal("0.0000045")) == "0.0000045"
    assert plain_decimal(Decimal("2.300")) == "2.3"


def test_the_document_carries_its_date_every_source_url_and_the_unpriced_gaps(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    model = _model(config, catalog)
    document = render_cost_model(model)
    assert f"**Generated on:** {PINNED_DATE}" in document
    assert "## Not priced by this model" in document
    assert "*(list)*" in document
    for rate in model.rates:
        assert f"[source]({rate.source_url})" in document
    for item in model.excluded:
        assert item.service in document


def test_a_breaching_document_states_the_failure_instead_of_hiding_it(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    shapes = load_shapes(config, REPO_ROOT).model_copy(update={"aca_max_replicas": 5})
    document = render_cost_model(build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT))
    assert "**Failures**" in document
    assert "| Container Apps maximum replicas | 1 | 5 | FAIL |" in document


def test_the_unpriced_os_disk_note_matches_what_the_aks_module_actually_commits(
    config: CostModelConfig,
) -> None:
    # If someone switches the pools to Ephemeral disks, the generated gap note must stop claiming
    # otherwise - so the claim is pinned to the committed value, not to prose.
    aks_module = (REPO_ROOT / "infra" / "terraform" / "modules" / "aks" / "main.tf").read_text(
        encoding="utf-8"
    )
    committed = read_assignment(
        REPO_ROOT / "infra" / "terraform" / "modules" / "aks" / "main.tf", "os_disk_type"
    )
    note = next(item for item in config.excluded if "OS disk" in item.service)
    assert f"os_disk_type = {committed}" in note.reason
    assert aks_module.count("os_disk_type") == 2


def test_the_document_states_that_the_cold_start_has_not_been_measured(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # The keep-warm cron is the one recurring line whose value is a measurement, and nothing in
    # this repository can produce that number. Printing a figure derived from
    # `cold_start_budget_seconds` would dress a configured allowance up as evidence.
    assert not config.cold_start.measured
    document = render_cost_model(_model(config, catalog))
    assert "**Not yet measured.**" in document
    assert "curl -o /dev/null" in document


def test_a_recorded_measurement_replaces_the_instructions_with_a_verdict(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    measured = config.model_copy(
        update={
            "cold_start": config.cold_start.model_copy(
                update={
                    "measured_at": "2026-09-20",
                    "cold_seconds": Decimal("24.5"),
                    "warm_seconds": Decimal("0.12"),
                }
            )
        }
    )
    document = render_cost_model(_model(measured, catalog))
    assert "**Not yet measured.**" not in document
    assert "| First request after idle (cold) | 24.5 |" in document
    assert "| Request immediately after (warm) | 0.12 |" in document
    assert "keep-warm earns its cost" in document


def test_a_cold_start_inside_the_threshold_says_to_turn_keep_warm_off(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # Below the threshold the cron costs ~$1.25/month to hide something nobody would notice.
    measured = config.model_copy(
        update={
            "cold_start": config.cold_start.model_copy(
                update={
                    "measured_at": "2026-09-20",
                    "cold_seconds": Decimal("3"),
                    "warm_seconds": Decimal("0.1"),
                }
            )
        }
    )
    assert "disable keep-warm" in render_cost_model(_model(measured, catalog))


def test_a_half_recorded_measurement_is_refused(tmp_path: Path) -> None:
    # A date with no numbers (or numbers with no date) reads as evidence it is not.
    root = stage_repo(tmp_path)
    document = root / "config" / "cost-model.yaml"
    payload = yaml.safe_load(document.read_text(encoding="utf-8"))
    payload["cold_start"]["measured_at"] = "2026-09-20"
    document.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(CostModelConfigError, match="cold_start"):
        load_cost_model_config(root)


def test_the_cli_writes_the_document_and_passes_on_the_committed_configuration(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cost-model.md"
    exit_code = azure_cost_plan.main(
        [
            "--prices-file",
            str(FROZEN_PRICES),
            "--date",
            PINNED_DATE,
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert "Azure Cost Model (generated)" in output.read_text(encoding="utf-8")


def test_the_cli_reproduces_the_committed_document_from_the_frozen_prices(tmp_path: Path) -> None:
    # The committed artifact must be exactly what the generator produces; a hand edit fails here.
    committed = GENERATED_DOC.read_text(encoding="utf-8")
    output = tmp_path / "cost-model.md"
    azure_cost_plan.main(
        ["--prices-file", str(FROZEN_PRICES), "--date", PINNED_DATE, "--output", str(output)]
    )
    assert output.read_text(encoding="utf-8") == committed


def test_the_cli_exits_non_zero_when_a_ceiling_is_breached(tmp_path: Path) -> None:
    root = stage_repo(tmp_path)
    payload = yaml.safe_load((root / "config" / "cost-model.yaml").read_text(encoding="utf-8"))
    payload["ceilings"]["aks_session_usd"] = "0.10"
    (root / "config" / "cost-model.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    exit_code = azure_cost_plan.main(
        [
            "--repo-root",
            str(root),
            "--prices-file",
            str(FROZEN_PRICES),
            "--date",
            PINNED_DATE,
            "--output",
            "cost-model.md",
        ]
    )
    assert exit_code == 1
    assert "session ceiling" in (root / "cost-model.md").read_text(encoding="utf-8")


def test_the_cli_reports_a_configuration_failure_without_writing(tmp_path: Path) -> None:
    exit_code = azure_cost_plan.main(
        ["--repo-root", str(tmp_path), "--prices-file", str(FROZEN_PRICES), "--print-only"]
    )
    assert exit_code == 2


def test_print_only_leaves_the_committed_document_untouched(tmp_path: Path) -> None:
    before = GENERATED_DOC.read_text(encoding="utf-8")
    assert (
        azure_cost_plan.main(
            ["--prices-file", str(FROZEN_PRICES), "--print-only", "--date", PINNED_DATE]
        )
        == 0
    )
    assert GENERATED_DOC.read_text(encoding="utf-8") == before


def test_the_cli_fetches_live_prices_and_can_save_them_as_a_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live path is exercised through the same seam the network call uses, so no request is
    # made and the saved fixture is proven to be replayable.
    frozen = load_price_items(FROZEN_PRICES)
    captured: dict[str, object] = {}

    def fake_fetch(
        config: CostModelConfig, regions: dict[str, str], skus: dict[str, str]
    ) -> tuple[RetailPriceItem, ...]:
        captured["regions"] = regions
        captured["skus"] = skus
        return frozen

    monkeypatch.setattr(azure_cost_plan, "fetch_price_items", fake_fetch)
    saved = tmp_path / "prices.json"
    exit_code = azure_cost_plan.main(
        [
            "--save-prices",
            str(saved),
            "--date",
            PINNED_DATE,
            "--output",
            str(tmp_path / "cost-model.md"),
        ]
    )
    assert exit_code == 0
    assert captured["regions"] == {"aca": "eastus2", "aks": "westus3"}
    assert captured["skus"] == {
        "aks_system_vm_size": "Standard_B2s",
        "aks_user_vm_size": "Standard_D2as_v4",
    }
    assert load_price_items(saved) == frozen
