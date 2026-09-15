"""Contracts for the Azure cost projection: committed shapes, resolved rates, and the ceilings.

Every case runs against a frozen retail-price fixture, so the arithmetic and the two enforced
ceilings are deterministic in CI and depend on neither a live price nor network access. The
projection is only worth committing if it fails loudly, so the ceiling breaches are asserted as
behavior rather than as formatting.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml
from azure_cost_inputs import FROZEN_PRICES, PINNED_DATE, REPO_ROOT, stage_repo

from lib.azure_cost.config import (
    CostModelConfig,
    CostModelConfigError,
    MeterSelector,
    load_cost_model_config,
)
from lib.azure_cost.model import CostModel, build_cost_model, project_aca, resolve_rates
from lib.azure_cost.prices import (
    PriceCatalog,
    PriceError,
    RetailPriceItem,
    dump_price_items,
    fetch_price_items,
    load_price_items,
)
from lib.azure_cost.shapes import ShapeError, load_shapes, read_assignment


@pytest.fixture
def config() -> CostModelConfig:
    return load_cost_model_config(REPO_ROOT)


@pytest.fixture
def catalog() -> PriceCatalog:
    return PriceCatalog(load_price_items(FROZEN_PRICES))


def _model(config: CostModelConfig, catalog: PriceCatalog) -> CostModel:
    shapes = load_shapes(config, REPO_ROOT)
    return build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT)


def test_the_committed_config_declares_both_enforced_ceilings(config: CostModelConfig) -> None:
    # These two are the whole point of the generator: without them it is a report, not a gate.
    assert config.ceilings.aks_session_usd == Decimal("5.00")
    assert config.ceilings.aca_max_replicas == 1


def test_an_unknown_config_key_fails_the_load_rather_than_being_ignored(tmp_path: Path) -> None:
    payload = yaml.safe_load((REPO_ROOT / "config" / "cost-model.yaml").read_text(encoding="utf-8"))
    payload["unexpected_key"] = True
    broken = tmp_path / "cost-model.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(CostModelConfigError, match="invalid"):
        load_cost_model_config(tmp_path, Path("cost-model.yaml"))


def test_a_missing_config_file_reports_the_path(tmp_path: Path) -> None:
    with pytest.raises(CostModelConfigError, match="unreadable"):
        load_cost_model_config(tmp_path, Path("absent.yaml"))


def test_shapes_come_from_the_committed_terraform_not_from_config(config: CostModelConfig) -> None:
    # A second copy of these values in config would drift silently from the sources that apply.
    shapes = load_shapes(config, REPO_ROOT)
    assert shapes.aca_region == "eastus"
    assert (shapes.aca_min_replicas, shapes.aca_max_replicas) == (0, 1)
    assert shapes.aca_vcpu == Decimal("0.5")
    assert shapes.aca_memory_gib == Decimal("1")
    assert shapes.log_daily_quota_gb == Decimal("0.1")
    assert shapes.aks_region == "westus3"
    assert shapes.aks_system_vm_size == "Standard_B2s"
    assert shapes.aks_user_vm_size == "Standard_D2as_v4"
    assert (shapes.aks_user_min_count, shapes.aks_user_max_count) == (1, 2)


def test_a_shape_that_is_not_assigned_fails_loudly(tmp_path: Path) -> None:
    source = tmp_path / "x.tfvars"
    source.write_text('location = "eastus"\n', encoding="utf-8")
    assert read_assignment(source, "location") == '"eastus"'
    with pytest.raises(ShapeError, match="min_replicas is not assigned"):
        read_assignment(source, "min_replicas")
    with pytest.raises(ShapeError, match="unreadable"):
        read_assignment(tmp_path / "absent.tfvars", "location")


def test_shape_loading_rejects_malformed_sources(config: CostModelConfig, tmp_path: Path) -> None:
    root = tmp_path
    (root / config.shapes.aca_tfvars.parent).mkdir(parents=True)
    (root / config.shapes.aca_tfvars).write_text(
        'location = "eastus"\nmin_replicas = zero\nmax_replicas = 1\n', encoding="utf-8"
    )
    with pytest.raises(ShapeError, match="min_replicas is not an integer"):
        load_shapes(config, root)


def test_a_blank_region_is_refused(config: CostModelConfig, tmp_path: Path) -> None:
    (tmp_path / config.shapes.aca_tfvars.parent).mkdir(parents=True)
    (tmp_path / config.shapes.aca_tfvars).write_text('location = ""\n', encoding="utf-8")
    with pytest.raises(ShapeError, match="location is blank"):
        load_shapes(config, tmp_path)


def test_the_recurring_projection_applies_the_free_grant_before_charging(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # 180,000 free vCPU-seconds at 0.5 vCPU and 360,000 GiB-seconds at 1 GiB both land on exactly
    # 100 warm replica-hours, so only the hours past that are billed - and at the idle rate.
    shapes = load_shapes(config, REPO_ROOT)
    aca = project_aca(config, shapes, resolve_rates(config, shapes, catalog))
    assert aca.free_grant_hours == Decimal("100")
    assert aca.warm_replica_hours == Decimal("176")
    assert aca.billable_hours == Decimal("76")
    assert aca.monthly_usd == Decimal("2.50")
    assert aca.log_ceiling_usd == Decimal("6.90")
    # Requests stay inside the 2M grant, so the meter contributes nothing.
    requests_line = next(line for line in aca.lines if line.item == "Requests")
    assert requests_line.amount_usd == Decimal("0.00")


def test_the_active_rate_ceiling_is_far_above_the_configured_projection(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # The active meter is 8x the idle one; the keep-warm design exists because of that gap.
    model = _model(config, catalog)
    assert model.aca.active_rate_ceiling_usd > model.aca.monthly_usd * 10


def test_one_aks_session_is_admitted_under_its_ceiling_with_the_adr_028_margin(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    model = _model(config, catalog)
    assert model.aks.session_usd == Decimal("0.79")
    assert model.aks.admission_margin == Decimal("0.30")
    assert model.aks.cost_with_margin_usd == Decimal("1.03")
    assert model.aks.ceiling_usd == Decimal("5.00")
    assert model.aks.admitted is True
    assert model.failures == ()


def test_a_session_over_the_ceiling_is_refused_and_reported(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # A 400-hour session is what a forgotten teardown looks like; it must not be admitted.
    long_session = config.model_copy(
        update={"aks_session": config.aks_session.model_copy(update={"hours": Decimal("400")})}
    )
    shapes = load_shapes(long_session, REPO_ROOT)
    model = build_cost_model(long_session, shapes, catalog, PINNED_DATE, REPO_ROOT)
    assert model.aks.admitted is False
    assert any("session ceiling" in failure for failure in model.failures)


def test_more_than_one_replica_breaches_the_container_apps_ceiling(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    shapes = load_shapes(config, REPO_ROOT).model_copy(update={"aca_max_replicas": 5})
    model = build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT)
    assert any("max_replicas is 5" in failure for failure in model.failures)


def test_every_rate_resolves_to_exactly_one_dated_sourced_price(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    model = _model(config, catalog)
    assert len(model.rates) == len(config.meters)
    for rate in model.rates:
        assert rate.price_usd > 0
        assert rate.source_url.startswith("https://")


def test_a_meter_the_retail_api_does_not_expose_falls_back_to_its_published_list_rate(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # Azure publishes no Load Balancer meter for westus3; assuming $0 would understate the session.
    model = _model(config, catalog)
    load_balancer = next(rate for rate in model.rates if rate.key == "standard_load_balancer")
    assert load_balancer.source == "published-list"
    assert load_balancer.price_usd == Decimal("0.025000")
    assert "load-balancer" in load_balancer.source_url


def test_an_unresolvable_meter_with_no_list_rate_is_an_error_not_a_zero(
    config: CostModelConfig,
) -> None:
    selector = MeterSelector(
        label="Absent meter",
        service_name="Nothing",
        unit_of_measure="1 Hour",
        region_source="aca",
    )
    with pytest.raises(PriceError, match="no retail row matched"):
        PriceCatalog(()).resolve("absent", selector, "eastus", "https://example.test")


def test_an_ambiguous_meter_is_an_error_rather_than_a_silent_pick(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # Dropping the tier selector leaves the three graduated Hot LRS tiers matching at once.
    ambiguous = config.meters["blob_hot_lrs"].model_copy(update={"tier_minimum_units": None})
    with pytest.raises(PriceError, match="retail rows matched"):
        catalog.resolve("blob_hot_lrs", ambiguous, "eastus", "https://example.test")


def test_a_selector_without_a_region_or_sku_is_refused(config: CostModelConfig) -> None:
    with pytest.raises(PriceError, match="no region supplied"):
        fetch_price_items(config, {}, {}, client=httpx.Client())
    with pytest.raises(PriceError, match="no SKU supplied"):
        fetch_price_items(config, {"aca": "eastus", "aks": "westus3"}, {}, client=httpx.Client())


def test_the_retail_query_paginates_and_filters_by_region_service_and_sku(
    config: CostModelConfig,
) -> None:
    frozen = json.loads(FROZEN_PRICES.read_text(encoding="utf-8"))["Items"]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("$filter", ""))
        if "page2" in str(request.url):
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(
            200, json={"Items": frozen[:1], "NextPageLink": "https://prices.test/page2"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    items = fetch_price_items(
        config,
        {"aca": "eastus", "aks": "westus3"},
        {"aks_system_vm_size": "Standard_B2s", "aks_user_vm_size": "Standard_D2as_v4"},
        client=client,
    )
    assert len(items) == len(config.meters)
    # A NextPageLink already embeds its own filter, so only the first request of each selector
    # carries $filter - and every one of those must pin region, service, and consumption pricing.
    filters = [clause for clause in seen if clause]
    assert len(filters) == len(config.meters)
    assert any("armRegionName eq 'westus3'" in clause for clause in filters)
    assert any("armSkuName eq 'Standard_D2as_v4'" in clause for clause in filters)
    assert all("priceType eq 'Consumption'" in clause for clause in filters)


def test_a_non_object_retail_response_is_refused(config: CostModelConfig) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[1, 2]))
    )
    with pytest.raises(PriceError, match="was not an object"):
        fetch_price_items(config, {"aca": "eastus", "aks": "westus3"}, {}, client=client)


def test_endless_pagination_is_bounded(config: CostModelConfig) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"Items": [], "NextPageLink": "https://prices.test/next"}
            )
        )
    )
    with pytest.raises(PriceError, match="pagination exceeded"):
        fetch_price_items(config, {"aca": "eastus", "aks": "westus3"}, {}, client=client)


def test_frozen_price_rows_round_trip(tmp_path: Path) -> None:
    items = load_price_items(FROZEN_PRICES)
    destination = tmp_path / "prices.json"
    dump_price_items(items, destination)
    assert load_price_items(destination) == items


def test_a_frozen_price_file_without_items_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "prices.json"
    broken.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(PriceError, match="no Items array"):
        load_price_items(broken)


def test_a_bare_price_array_is_also_accepted(tmp_path: Path) -> None:
    rows = json.loads(FROZEN_PRICES.read_text(encoding="utf-8"))["Items"]
    bare = tmp_path / "prices.json"
    bare.write_text(json.dumps(rows), encoding="utf-8")
    assert len(load_price_items(bare)) == len(rows)


def test_a_retail_item_tolerates_unknown_api_fields() -> None:
    item = RetailPriceItem.model_validate(
        {
            "armRegionName": "eastus",
            "serviceName": "Storage",
            "meterName": "Hot LRS Data Stored",
            "productName": "General Block Blob v2",
            "unitOfMeasure": "1 GB/Month",
            "retailPrice": "0.0208",
            "someFutureField": "ignored",
        }
    )
    assert item.retail_price == Decimal("0.0208")
    assert item.tier_minimum_units == Decimal("0")


def test_shape_loading_reports_every_malformed_terraform_source(tmp_path: Path) -> None:
    config = load_cost_model_config(REPO_ROOT)
    root = stage_repo(tmp_path)
    observability = root / config.shapes.observability_module
    observability.write_text("daily_quota_gb = lots\n", encoding="utf-8")
    with pytest.raises(ShapeError, match="daily_quota_gb is not a number"):
        load_shapes(config, root)

    gateway = root / config.shapes.gateway_module
    gateway.write_text('variable "cpu" {\n  type = number\n}\n', encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu has no default"):
        load_shapes(config, root)

    gateway.write_text('variable "cpu" {\n  default = half\n}\n', encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu default is not a number"):
        load_shapes(config, root)

    gateway.write_text("# no variables here\n", encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu is not declared"):
        load_shapes(config, root)


def test_memory_declared_in_mebibytes_is_normalized_to_gibibytes(tmp_path: Path) -> None:
    # The retail meters bill per GiB-second, so a "512Mi" shape must not be priced as 512 GiB.
    config = load_cost_model_config(REPO_ROOT)
    root = stage_repo(tmp_path)
    (root / config.shapes.gateway_module).write_text(
        'variable "cpu" {\n  default = 0.25\n}\n\nvariable "memory" {\n  default = "512Mi"\n}\n',
        encoding="utf-8",
    )
    shapes = load_shapes(config, root)
    assert shapes.aca_vcpu == Decimal("0.25")
    assert shapes.aca_memory_gib == Decimal("0.5")
