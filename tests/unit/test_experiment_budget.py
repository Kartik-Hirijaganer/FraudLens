"""Behavioral tests for budget policy, pilot admission, and ledger reconciliation."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import experiment_budget
from lib.experiments.budget import (
    BudgetConfig,
    LedgerEntry,
    PilotMeasurement,
    admit,
    check_ledger,
    load_budget_config,
    load_ledger,
    project_cost,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_budget_has_the_exact_ceiling_allocations_margin_and_quotes() -> None:
    config = load_budget_config(REPO_ROOT)
    assert config.ceiling_usd == Decimal("75.00")
    assert config.admission_margin == Decimal("0.30")
    assert config.allocations == {
        "azure_cpu_batch": Decimal("15.00"),
        "gpu_benchmark": Decimal("25.00"),
        "e2e_application_pass": Decimal("5.00"),
        "supporting_resources": Decimal("5.00"),
        "reserve": Decimal("25.00"),
    }
    assert set(config.watchdog_hours) == set(config.allocations) - {"reserve"}
    assert config.rates["azure_nc24ads_a100_v4_spot"].hourly_rate_usd == Decimal("0.678770")
    runpod_quote = config.rates["runpod_rtx4090_secure_payg"]
    assert runpod_quote.provider == "runpod"
    assert runpod_quote.region == "secure-cloud"
    assert runpod_quote.hourly_rate_usd == Decimal("0.740000")
    azure_quotes = set(config.rates) - {"runpod_rtx4090_secure_payg"}
    assert config.rates["azure_e16ads_v5_payg"].hourly_rate_usd == Decimal("1.048000")
    assert config.rates["azure_e16ads_v5_spot"].hourly_rate_usd == Decimal("0.193670")
    assert config.rates["azure_b2s_payg"].hourly_rate_usd == Decimal("0.041600")
    assert config.rates["azure_d2as_v5_spot"].hourly_rate_usd == Decimal("0.015893")
    for key, quote in config.rates.items():
        if key == "runpod_rtx4090_secure_payg":
            assert str(quote.price_source_url) == "https://www.runpod.io/pricing"
        else:
            assert quote.provider == "azure"
            assert str(quote.price_source_url).startswith("https://prices.azure.com/")
    for key in azure_quotes:
        assert config.rates[key].region == "westus3"
        assert config.rates[key].price_verified_at == date(2026, 9, 14)


def test_projection_scales_pilot_work_and_admission_applies_margin() -> None:
    projection = project_cost(
        [
            PilotMeasurement(
                rate_key="test",
                completed_units=Decimal("100"),
                target_units=Decimal("1000"),
                elapsed_hours=Decimal("0.5"),
                hourly_rate_usd=Decimal("2"),
            )
        ]
    )
    assert projection.projected_hours == Decimal("5.0")
    assert projection.projected_cost_usd == Decimal("10.0")
    assert admit(projection, Decimal("13")).admitted is True
    refusal = admit(projection, Decimal("12.99"))
    assert refusal.admitted is False
    assert refusal.reason == "projection_exceeds_allocation"


def test_projection_rejects_empty_input_and_admission_rejects_bad_bounds() -> None:
    with pytest.raises(ValueError, match="at least one"):
        project_cost([])
    projection = project_cost(
        [
            PilotMeasurement(
                rate_key="test",
                completed_units=Decimal("1"),
                target_units=Decimal("1"),
                elapsed_hours=Decimal("1"),
                hourly_rate_usd=Decimal("1"),
            )
        ]
    )
    with pytest.raises(ValueError, match="allocation"):
        admit(projection, Decimal("0"))
    with pytest.raises(ValueError, match="admission_margin"):
        admit(projection, Decimal("1"), admission_margin=Decimal("1"))


def test_budget_rejects_allocation_drift() -> None:
    path = REPO_ROOT / "config" / "experiments" / "budget.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["allocations"]["reserve"] = "24.00"
    with pytest.raises(ValidationError, match="sum exactly"):
        BudgetConfig.model_validate(payload)


def test_committed_ledger_covers_all_published_reports() -> None:
    config = load_budget_config(REPO_ROOT)
    entries = load_ledger(REPO_ROOT / "docs" / "reference" / "experiments" / "ledger.md")
    assert {entry.run_id for entry in entries} == {
        "data-batch-20260914-pilot1",
        "gfp-c41b1fbb266f44d4",
        "sar-eval-e5c9a36b5f8a33f3",
        "vllm-bench-f810b57a7b8ae05a",
    }
    batch = next(entry for entry in entries if entry.run_id == "data-batch-20260914-pilot1")
    assert batch.evidence_run_ids == ("fulldata-b55c4ae63ed8bbae",)
    assert check_ledger(config, entries, REPO_ROOT / "docs/reference/benchmarks") == []


def _current_entry(**changes: object) -> LedgerEntry:
    values: dict[str, object] = {
        "session_date": date(2026, 9, 13),
        "provider": "azure",
        "sku": "test-sku",
        "purchase_option": "spot",
        "started_at": "2026-09-13T10:00:00Z",
        "stopped_at": "2026-09-13T11:00:00Z",
        "hours": Decimal("1"),
        "quoted_rate_usd": Decimal("1"),
        "projected_cost_usd": Decimal("16"),
        "actual_cost_usd": None,
        "run_id": "paid-run",
        "budget_scope": "current-plan",
        "allocation": "azure_cpu_batch",
        "teardown_verified": "no",
    }
    values.update(changes)
    return LedgerEntry.model_validate(values)


def test_ledger_rejects_allocation_overrun_missing_teardown_and_missing_report(
    sandbox: Path,
) -> None:
    report = sandbox / "report.json"
    report.write_text(json.dumps({"runId": "unrecorded-run"}), encoding="utf-8")
    errors = check_ledger(load_budget_config(REPO_ROOT), [_current_entry()], sandbox)
    assert any("allocation azure_cpu_batch" in error for error in errors)
    assert any("lacks teardown verification" in error for error in errors)
    assert any("unrecorded-run" in error for error in errors)


def test_cli_estimate_admit_and_ledger_check_are_machine_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    estimate_args = [
        "estimate",
        "--rate",
        "azure_e16ads_v5_spot",
        "--pilot-hours",
        "1",
        "--pilot-units",
        "100",
        "--target-units",
        "1000",
    ]
    assert experiment_budget.main(estimate_args) == 0
    assert Decimal(json.loads(capsys.readouterr().out)["projected_cost_usd"]) > 0

    admit_args = [
        "admit",
        "--allocation",
        "azure_cpu_batch",
        "--rate",
        "azure_e16ads_v5_spot",
        "--pilot-hours",
        "0.1",
        "--pilot-units",
        "100",
        "--target-units",
        "1000",
    ]
    assert experiment_budget.main(admit_args) == 0
    assert json.loads(capsys.readouterr().out)["admitted"] is True

    assert experiment_budget.main(["ledger-check"]) == 0
    assert "ledger-check OK" in capsys.readouterr().out
