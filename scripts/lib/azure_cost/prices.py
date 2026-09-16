"""Summary: Read-only Azure Retail Prices client and deterministic meter resolution. One
`$filter` query is issued per configured selector, paginated to completion, and every returned
row must narrow to exactly one price: zero matches or several are a failure, never a silent pick.
A selector carrying a published `list_price_usd` falls back to it when the API exposes no meter,
and the resolved rate records which of the two it came from so the report can mark it.

Key classes:
- RetailPriceItem: one price row as the retail API returns it.
- ResolvedRate: one selector narrowed to a single dated, sourced rate.
- PriceCatalog: the fetched or frozen rows, resolved by selector.
- PriceError: safe failure when a selector cannot narrow to exactly one row.

Key functions:
- fetch_price_items: query the retail API for every selector (network, read-only); accepts an
  injected client so the query path is testable without network access.
- load_price_items: read frozen price rows from a JSON file for deterministic runs.
- dump_price_items: write fetched rows back out as a reusable frozen fixture.

Notes:
- The retail API is public and unauthenticated; no credential is used or required, so this
  module is safe under Golden Rule 7 (it creates nothing and mutates nothing).
- `extra="ignore"` on RetailPriceItem keeps unrelated API fields from breaking the parse.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lib.azure_cost.config import CostModelConfig, MeterSelector

_MAX_PAGES = 20
RateSource = Literal["retail-api", "published-list"]


class PriceError(RuntimeError):
    """Raised when a meter selector does not resolve to exactly one retail price."""


class RetailPriceItem(BaseModel):
    """One price row exactly as the Azure Retail Prices API returns it."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    arm_region_name: str = Field(..., alias="armRegionName", description="ARM region name.")
    service_name: str = Field(..., alias="serviceName", description="Retail service name.")
    meter_name: str = Field(..., alias="meterName", description="Retail meter name.")
    product_name: str = Field(..., alias="productName", description="Retail product name.")
    sku_name: str = Field(default="", alias="skuName", description="Retail SKU name.")
    arm_sku_name: str = Field(default="", alias="armSkuName", description="ARM compute SKU name.")
    unit_of_measure: str = Field(..., alias="unitOfMeasure", description="Billed unit of measure.")
    retail_price: Decimal = Field(..., ge=0, alias="retailPrice", description="Unit price in USD.")
    tier_minimum_units: Decimal = Field(
        default=Decimal("0"), alias="tierMinimumUnits", description="Graduated-tier lower bound."
    )
    effective_start_date: str = Field(
        default="", alias="effectiveStartDate", description="Provider rate effective date."
    )
    price_type: str = Field(default="", alias="type", description="Consumption or reservation.")


class ResolvedRate(BaseModel):
    """One selector narrowed to a single dated, sourced rate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(..., min_length=1, description="Stable selector key from the config.")
    label: str = Field(..., min_length=1, description="Human-readable meter name.")
    region: str = Field(..., min_length=1, description="Region the rate applies to.")
    unit_of_measure: str = Field(..., min_length=1, description="Billed unit of measure.")
    price_usd: Decimal = Field(..., ge=0, description="Resolved unit price in USD.")
    source: RateSource = Field(..., description="Whether the rate came from the API or a list.")
    source_url: str = Field(..., min_length=1, description="Provenance URL for the rate.")
    effective_from: str = Field(..., description="Provider-reported effective date when known.")


def _filter_clauses(selector: MeterSelector, region: str, arm_sku_name: str | None) -> list[str]:
    clauses = [
        f"armRegionName eq '{region}'",
        f"serviceName eq '{selector.service_name}'",
        "priceType eq 'Consumption'",
    ]
    if selector.meter_name is not None:
        clauses.append(f"meterName eq '{selector.meter_name}'")
    if selector.product_name is not None:
        clauses.append(f"productName eq '{selector.product_name}'")
    if arm_sku_name is not None:
        clauses.append(f"armSkuName eq '{arm_sku_name}'")
    return clauses


def _page(client: httpx.Client, url: str, params: dict[str, str] | None) -> dict[str, Any]:
    response = client.get(url, params=params)
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise PriceError(f"retail price response was not an object: {url}")
    return payload


def _query(
    client: httpx.Client, config: CostModelConfig, selector: MeterSelector, region: str, sku: str
) -> list[RetailPriceItem]:
    params: dict[str, str] | None = {
        "api-version": config.retail_prices.api_version,
        "currencyCode": config.retail_prices.currency,
        "$filter": " and ".join(_filter_clauses(selector, region, sku or None)),
    }
    url = str(config.retail_prices.endpoint)
    items: list[RetailPriceItem] = []
    for _ in range(_MAX_PAGES):
        payload = _page(client, url, params)
        raw = payload.get("Items", [])
        items.extend(RetailPriceItem.model_validate(entry) for entry in raw)
        next_link = payload.get("NextPageLink")
        if not isinstance(next_link, str) or not next_link:
            return items
        url, params = next_link, None
    raise PriceError(f"retail price pagination exceeded {_MAX_PAGES} pages for {selector.label}")


def _selector_region(selector: MeterSelector, regions: dict[str, str]) -> str:
    region = regions.get(selector.region_source)
    if region is None:
        raise PriceError(f"no region supplied for {selector.region_source}")
    return region


def _selector_sku(selector: MeterSelector, skus: dict[str, str]) -> str:
    if selector.arm_sku_name_source is None:
        return ""
    sku = skus.get(selector.arm_sku_name_source)
    if sku is None:
        raise PriceError(f"no SKU supplied for {selector.arm_sku_name_source}")
    return sku


def fetch_price_items(
    config: CostModelConfig,
    regions: dict[str, str],
    skus: dict[str, str],
    *,
    client: httpx.Client | None = None,
) -> tuple[RetailPriceItem, ...]:
    """Query the public retail API once per selector and return every returned row."""
    owned = client is None
    active = client or httpx.Client(timeout=config.retail_prices.timeout_seconds)
    try:
        items: list[RetailPriceItem] = []
        for selector in config.meters.values():
            region = _selector_region(selector, regions)
            items.extend(_query(active, config, selector, region, _selector_sku(selector, skus)))
        return tuple(items)
    finally:
        if owned:
            active.close()


def load_price_items(path: Path) -> tuple[RetailPriceItem, ...]:
    """Read frozen retail price rows from a JSON file so a run is fully deterministic."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("Items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise PriceError(f"frozen price file has no Items array: {path}")
    return tuple(RetailPriceItem.model_validate(entry) for entry in rows)


def dump_price_items(items: Iterable[RetailPriceItem], path: Path) -> None:
    """Write fetched rows out as a reusable frozen fixture in stable field order."""
    payload = {"Items": [item.model_dump(by_alias=True, mode="json") for item in items]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class PriceCatalog:
    """The fetched or frozen retail rows, resolved one selector at a time."""

    def __init__(self, items: Sequence[RetailPriceItem]) -> None:
        self._items = tuple(items)

    @property
    def items(self) -> tuple[RetailPriceItem, ...]:
        """Every price row the catalog holds."""
        return self._items

    def _candidates(
        self, selector: MeterSelector, region: str, arm_sku_name: str
    ) -> list[RetailPriceItem]:
        matches = [
            item
            for item in self._items
            if item.arm_region_name == region
            and item.service_name == selector.service_name
            and item.unit_of_measure == selector.unit_of_measure
            and (selector.meter_name is None or item.meter_name == selector.meter_name)
            and (selector.product_name is None or item.product_name == selector.product_name)
            and (not arm_sku_name or item.arm_sku_name == arm_sku_name)
            and (
                selector.tier_minimum_units is None
                or item.tier_minimum_units == selector.tier_minimum_units
            )
            and (
                selector.min_retail_price is None or item.retail_price >= selector.min_retail_price
            )
            and not any(
                fragment in item.product_name for fragment in selector.exclude_product_substrings
            )
            and not any(
                fragment in item.meter_name for fragment in selector.exclude_meter_substrings
            )
        ]
        return sorted(matches, key=lambda item: (item.retail_price, item.meter_name))

    def resolve(
        self,
        key: str,
        selector: MeterSelector,
        region: str,
        endpoint: str,
        arm_sku_name: str = "",
    ) -> ResolvedRate:
        """Narrow one selector to exactly one rate, or fall back to its published list rate."""
        matches = self._candidates(selector, region, arm_sku_name)
        if len(matches) == 1:
            item = matches[0]
            return ResolvedRate(
                key=key,
                label=selector.label,
                region=region,
                unit_of_measure=item.unit_of_measure,
                price_usd=item.retail_price,
                source="retail-api",
                source_url=endpoint,
                effective_from=item.effective_start_date,
            )
        if matches:
            raise PriceError(
                f"{selector.label}: {len(matches)} retail rows matched in {region}; "
                "narrow the selector so exactly one price can be used"
            )
        if selector.list_price_usd is None or selector.list_price_source_url is None:
            raise PriceError(
                f"{selector.label}: no retail row matched in {region} and no published "
                "list_price_usd is configured"
            )
        return ResolvedRate(
            key=key,
            label=selector.label,
            region=region,
            unit_of_measure=selector.unit_of_measure,
            price_usd=selector.list_price_usd,
            source="published-list",
            source_url=str(selector.list_price_source_url),
            effective_from="",
        )
