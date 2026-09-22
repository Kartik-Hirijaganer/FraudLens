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
    # App-level, not per-revision: blue/green promotion runs two revisions at once, so one
    # would fail every deploy and two is the real bound.
    assert config.ceilings.aca_max_replicas == 2
    assert config.ceilings.aca_concurrent_revisions == 2


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
    assert shapes.aca_region == "eastus2"
    assert (shapes.aca_min_replicas, shapes.aca_max_replicas) == (1, 1)
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
    assert aca.warm_replica_hours == Decimal("720")
    assert aca.billable_hours == Decimal("620")
    assert aca.monthly_usd == Decimal("11.53")
    assert aca.log_ceiling_usd == Decimal("8.28")
    # Requests stay inside the 2M grant, so the meter contributes nothing.
    requests_line = next(line for line in aca.lines if line.item == "Requests")
    assert requests_line.amount_usd == Decimal("0.00")


def test_the_active_rate_ceiling_stays_above_the_committed_projection(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # The active meter is 8x the idle one. The ceiling prices max_replicas running ACTIVE every
    # hour of the month, so it does NOT move with min_replicas — it was the same figure under
    # scale-to-zero. What moved is the projection: committing a warm replica took it from roughly
    # 6% of that worst case to roughly 27%. The margin is genuinely smaller, so the bound is
    # restated at what now holds rather than left at a multiple the shape no longer supports.
    model = _model(config, catalog)
    assert model.aca.active_rate_ceiling_usd > model.aca.monthly_usd * 3


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


def test_revision_mode_decides_whether_max_replicas_bounds_the_app(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    """The regression guard for the 2026-09-22 budget alert.

    `max_replicas = 1` was reported as a satisfied app-level ceiling while TWO replicas ran:
    revision `fraudlens-prod-api--0000019` failed activation still holding a replica, took 0%
    traffic, and so was never scaled away. The per-revision limit was never an app-level bound
    under `revision_mode = Multiple`, and the gate could not see the difference.
    """
    base = load_shapes(config, REPO_ROOT)
    assert base.aca_max_replicas == 1, "this test is about a per-revision limit of one"

    multiple = base.model_copy(update={"aca_revision_mode": "Multiple"})
    single = base.model_copy(update={"aca_revision_mode": "Single"})

    multi_model = build_cost_model(config, multiple, catalog, PINNED_DATE, REPO_ROOT)
    single_model = build_cost_model(config, single, catalog, PINNED_DATE, REPO_ROOT)

    # Same per-revision limit, different app-level truth.
    assert multi_model.aca.effective_max_replicas == 2
    assert single_model.aca.effective_max_replicas == 1

    # And the worst-case bill follows the app, not one revision of it.
    assert multi_model.aca.active_rate_ceiling_usd > single_model.aca.active_rate_ceiling_usd


def test_a_one_replica_app_ceiling_rejects_multiple_revision_mode(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    # The exact claim the old document made -- "max_replicas cannot be exceeded", ceiling 1 --
    # must now fail loudly instead of reporting PASS.
    strict = config.model_copy(
        update={"ceilings": config.ceilings.model_copy(update={"aca_max_replicas": 1})}
    )
    shapes = load_shapes(config, REPO_ROOT)
    assert shapes.aca_revision_mode == "Multiple"
    model = build_cost_model(strict, shapes, catalog, PINNED_DATE, REPO_ROOT)
    assert model.failures, "a 1-replica app ceiling under Multiple mode must not pass"
    assert any("app-level replicas reach 2" in failure for failure in model.failures)


def test_more_than_one_replica_breaches_the_container_apps_ceiling(
    config: CostModelConfig, catalog: PriceCatalog
) -> None:
    shapes = load_shapes(config, REPO_ROOT).model_copy(update={"aca_max_replicas": 5})
    model = build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT)
    # 5 per revision x 2 concurrent revisions = 10 app-level, against a ceiling of 2.
    assert model.aca.effective_max_replicas == 10
    assert any("app-level replicas reach 10" in failure for failure in model.failures)


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
        catalog.resolve("blob_hot_lrs", ambiguous, "eastus2", "https://example.test")


def _offline_client() -> httpx.Client:
    """Return a client that answers every retail query with no rows and reaches no network.

    The refusal being asserted below is about a MISSING selector, not about the API. The second
    case still has to walk the region-keyed meters before it reaches a SKU-keyed one, so a live
    client would issue real requests to prices.azure.com on the way to the assertion -- which
    made this unit test depend on an external service and eventually got CI rate-limited (429).
    """
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"Items": []}))
    )


def test_a_selector_without_a_region_or_sku_is_refused(config: CostModelConfig) -> None:
    with _offline_client() as client, pytest.raises(PriceError, match="no region supplied"):
        fetch_price_items(config, {}, {}, client=client)
    with _offline_client() as client, pytest.raises(PriceError, match="no SKU supplied"):
        fetch_price_items(config, {"aca": "eastus", "aks": "westus3"}, {}, client=client)


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


# Every synthetic gateway module needs the mode: it decides whether max_replicas bounds the
# app or one revision of it, so the loader refuses to guess when it is absent.
_GATEWAY_MODE = 'revision_mode = "Multiple"\n\n'


def test_an_absent_revision_mode_fails_rather_than_defaulting(tmp_path: Path) -> None:
    # Defaulting to Single here would silently restore the bug this shape exists to prevent:
    # it would report a 1-replica app bound while Multiple mode allowed more.
    config = load_cost_model_config(REPO_ROOT)
    root = stage_repo(tmp_path)
    (root / config.shapes.gateway_module).write_text(
        'variable "cpu" {\n  default = 0.5\n}\n', encoding="utf-8"
    )
    with pytest.raises(ShapeError, match="revision_mode is not assigned"):
        load_shapes(config, root)


def test_shape_loading_reports_every_malformed_terraform_source(tmp_path: Path) -> None:
    config = load_cost_model_config(REPO_ROOT)
    root = stage_repo(tmp_path)
    observability = root / config.shapes.observability_module
    observability.write_text("daily_quota_gb = lots\n", encoding="utf-8")
    with pytest.raises(ShapeError, match="daily_quota_gb is not a number"):
        load_shapes(config, root)

    gateway = root / config.shapes.gateway_module
    gateway.write_text(_GATEWAY_MODE + 'variable "cpu" {\n  type = number\n}\n', encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu has no default"):
        load_shapes(config, root)

    gateway.write_text(_GATEWAY_MODE + 'variable "cpu" {\n  default = half\n}\n', encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu default is not a number"):
        load_shapes(config, root)

    gateway.write_text(_GATEWAY_MODE + "# no variables here\n", encoding="utf-8")
    with pytest.raises(ShapeError, match="variable cpu is not declared"):
        load_shapes(config, root)


def test_memory_declared_in_mebibytes_is_normalized_to_gibibytes(tmp_path: Path) -> None:
    # The retail meters bill per GiB-second, so a "512Mi" shape must not be priced as 512 GiB.
    config = load_cost_model_config(REPO_ROOT)
    root = stage_repo(tmp_path)
    (root / config.shapes.gateway_module).write_text(
        _GATEWAY_MODE
        + 'variable "cpu" {\n  default = 0.25\n}\n\nvariable "memory" {\n  default = "512Mi"\n}\n',
        encoding="utf-8",
    )
    shapes = load_shapes(config, root)
    assert shapes.aca_vcpu == Decimal("0.25")
    assert shapes.aca_memory_gib == Decimal("0.5")
