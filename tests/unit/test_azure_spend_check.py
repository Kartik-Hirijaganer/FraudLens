"""Behavioural suite for the daily spend attribution gate.

The check this covers replaced one that never ran: the previous workflow step called
`az costmanagement query`, a command that does not exist, and swallowed the failure into
`|| echo "0"`. It reported a passing $0.00 every day, including through the 2026-09-22 budget
alert it existed to pre-empt. Most of what follows is therefore about the failure paths — an
unreadable response must fail, never silently become zero.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from azure_cost_inputs import FROZEN_PRICES, PINNED_DATE, REPO_ROOT

import check_azure_spend
from check_azure_spend import SpendError, classify, main, parse_query_result
from lib.azure_cost.config import load_cost_model_config
from lib.azure_cost.model import build_cost_model
from lib.azure_cost.prices import PriceCatalog, load_price_items
from lib.azure_cost.shapes import load_shapes


def _response(rows: list[list[object]], columns: list[str] | None = None) -> dict:
    names = columns or ["Cost", "ResourceGroupName", "Currency"]
    return {"properties": {"columns": [{"name": n} for n in names], "rows": rows}}


def _policy():
    return load_cost_model_config(REPO_ROOT).spend_watchdog


# The month as Azure actually reported it when the budget alert fired.
_SEPTEMBER = [
    [10.9173, "fraudlens-data-batch-rg", "USD"],
    [1.2360, "fraudlens-prod-rg", "USD"],
    [0.9039, "fraudlens-aks-demo-nodes-rg", "USD"],
    [0.0018, "fraudlens-tfstate-rg", "USD"],
]


def test_the_alerting_month_splits_into_governed_and_recurring_spend() -> None:
    """The split the $12.51 alert could not make on its own.

    80% of that month was ledgered experiment spend whose resources were already destroyed. A
    total alone cannot say that, which is why answering it took a manual investigation.
    """
    report = classify(parse_query_result(_response(_SEPTEMBER)), _policy())
    assert report.recurring_usd == Decimal("1.2378")
    assert report.ephemeral_usd == Decimal("11.8212")
    assert report.unattributed_usd == Decimal("0")
    assert report.total_usd == Decimal("13.0590")
    # The governed 80% must not count against the recurring threshold.
    assert report.recurring_usd < _policy().recurring_monthly_usd


def test_recurring_drift_fails_while_the_same_total_in_experiments_does_not() -> None:
    policy = _policy()
    drifted = classify(parse_query_result(_response([[26.0, "fraudlens-prod-rg", "USD"]])), policy)
    governed = classify(
        parse_query_result(_response([[26.0, "fraudlens-data-batch-rg", "USD"]])), policy
    )
    assert drifted.recurring_usd > policy.recurring_monthly_usd
    assert governed.recurring_usd == Decimal("0")


def test_a_group_nobody_declared_is_unattributed_rather_than_ignored() -> None:
    # The case the subscription-wide budget exists for: spend in a group no root owns.
    report = classify(parse_query_result(_response([[7.5, "mystery-rg", "USD"]])), _policy())
    assert [row.resource_group for row in report.unattributed] == ["mystery-rg"]
    assert report.unattributed_usd == Decimal("7.5")


def test_resource_groups_match_regardless_of_casing() -> None:
    # Cost Management does not promise the portal's casing; a case flip must not silently
    # reclassify a known group as unattributed and fail the watchdog for no reason.
    shouting = [[1.0, "FRAUDLENS-PROD-RG", "USD"], [2.0, "networkwatcherrg", "USD"]]
    report = classify(parse_query_result(_response(shouting)), _policy())
    assert report.recurring_usd == Decimal("1.0")
    assert [row.resource_group for row in report.system] == ["networkwatcherrg"]
    assert report.unattributed == ()


def test_columns_are_read_by_name_so_a_reordered_response_still_parses() -> None:
    reordered = _response(
        [["fraudlens-prod-rg", "USD", 3.25]], columns=["ResourceGroupName", "Currency", "Cost"]
    )
    rows = parse_query_result(reordered)
    assert rows[0].resource_group == "fraudlens-prod-rg"
    assert rows[0].cost_usd == Decimal("3.25")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "not a JSON object"),
        ({"properties": []}, "no properties object"),
        ({"properties": {}}, "no columns/rows arrays"),
        ({"properties": {"columns": [{"name": "Cost"}], "rows": []}}, "missing the Resource"),
        ({"properties": {"columns": [{"name": "ResourceGroupName"}], "rows": []}}, "missing the "),
    ],
)
def test_a_malformed_response_is_an_error_not_an_empty_result(payload, message) -> None:
    with pytest.raises(SpendError, match=message):
        parse_query_result(payload)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (["only-one-cell"], "malformed"),
        ([1.0, "", "USD"], "no resource group"),
        ([1.0, None, "USD"], "no resource group"),
        (["not-a-number", "fraudlens-prod-rg", "USD"], "non-numeric"),
    ],
)
def test_a_malformed_row_is_an_error_not_a_skipped_line(row, message) -> None:
    with pytest.raises(SpendError, match=message):
        parse_query_result(_response([row]))


def test_an_empty_response_fails_instead_of_reporting_zero_spend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact regression that made the predecessor inert.

    `az costmanagement query` does not exist, so the old step's `2>/dev/null || echo "0"` made
    every run report $0.00 and pass. An unreadable response is a failed check.
    """
    empty = tmp_path / "mtd.json"
    empty.write_text("", encoding="utf-8")
    assert main(["--input", str(empty)]) == 2
    assert "did not return" in capsys.readouterr().out


def test_invalid_json_fails_rather_than_being_treated_as_no_spend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "mtd.json"
    broken.write_text("<html>gateway timeout</html>", encoding="utf-8")
    assert main(["--input", str(broken)]) == 2
    assert "invalid JSON" in capsys.readouterr().out


def test_the_cli_passes_on_the_alerting_month_and_names_every_bucket(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "mtd.json"
    payload.write_text(json.dumps(_response(_SEPTEMBER)), encoding="utf-8")
    assert main(["--input", str(payload)]) == 0
    out = capsys.readouterr().out
    for label in ("recurring", "ephemeral (ledgered)", "system (free)", "UNATTRIBUTED", "TOTAL"):
        assert label in out
    assert "azure-spend-check OK" in out


def test_the_cli_exits_non_zero_and_annotates_on_recurring_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "mtd.json"
    payload.write_text(json.dumps(_response([[26.0, "fraudlens-prod-rg", "USD"]])), "utf-8")
    assert main(["--input", str(payload)]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    # The annotation must say it will not stop on its own; that is what separates it from a
    # torn-down experiment and tells the reader to act now rather than wait.
    assert "will not stop on its own" in out


def test_the_cli_exits_non_zero_on_an_undeclared_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "mtd.json"
    payload.write_text(json.dumps(_response([[3.0, "surprise-rg", "USD"]])), "utf-8")
    assert main(["--input", str(payload)]) == 1
    assert "surprise-rg" in capsys.readouterr().out


def test_the_cli_reads_stdin_when_no_input_path_is_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = io.StringIO(json.dumps(_response(_SEPTEMBER)))
    monkeypatch.setattr(check_azure_spend.sys, "stdin", stdin)
    assert main([]) == 0
    assert "azure-spend-check OK" in capsys.readouterr().out


def test_the_recurring_threshold_stays_above_the_generated_projection() -> None:
    """Ties the threshold to the cost model instead of copying it (rule 5).

    The threshold is a committed number, not a second copy of the projection. This assertion is
    the link between them: grow the recurring projection past the threshold and CI fails here,
    forcing a deliberate decision rather than a gate that quietly stops meaning anything.
    """
    config = load_cost_model_config(REPO_ROOT)
    shapes = load_shapes(config, REPO_ROOT)
    catalog = PriceCatalog(load_price_items(FROZEN_PRICES))
    model = build_cost_model(config, shapes, catalog, PINNED_DATE, REPO_ROOT)
    assert config.spend_watchdog.recurring_monthly_usd > model.fixed_monthly_usd


def test_the_declared_group_lists_do_not_overlap() -> None:
    # A group in two lists would classify by list order — silently, and differently depending on
    # which list someone edited last.
    policy = _policy()
    seen: set[str] = set()
    for names in (
        policy.recurring_resource_groups,
        policy.ephemeral_resource_groups,
        policy.system_resource_groups,
    ):
        folded = {name.casefold() for name in names}
        assert not (folded & seen), "a resource group appears in more than one watchdog list"
        seen |= folded
