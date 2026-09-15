"""Summary: Functional vLLM application-pass contracts with an in-memory HTTP transport.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- The fake API preserves the real readiness, ingest, queue, snapshot, and alert-detail shapes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from fraudlens_core.phi import mask_identifier
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
from lib.sar_eval.scenarios import generate_scenarios
from lib.vllm_bench.e2e import (
    E2eApplicationReport,
    E2eCaseOutcome,
    _namespaced_scenario,
    run_e2e,
)

_RUN_ID = "vllm-e2e-0123456789abcdef"
_MODEL = "xgb-ibm-aml-hi-medium-5835992a6919"


def _structured(citation: str) -> dict[str, object]:
    return {
        "subject": "Synthetic activity",
        "narrative": "Synthetic activity warrants analyst review.",
        "claims": [],
        "sections": [],
        "citedRegulations": [citation],
        "recommendedAction": "Review the synthetic evidence.",
    }


def _transport(*, provider: str = "vllm", attempt: int = 1) -> httpx.MockTransport:
    state: dict[str, dict[str, object]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/readyz":
            return httpx.Response(
                503,
                json={
                    "status": "not_ready",
                    "checks": [{"name": "llmProvider", "status": "ok", "detail": provider}],
                },
            )
        if path == "/api/v1/transactions" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(201, json={"transactionId": f"txn-{body['externalId']}"})
        if (
            path.startswith("/api/v1/dev/transactions/")
            and path.endswith("/synthetic-provenance")
            and request.method == "POST"
        ):
            return httpx.Response(
                200,
                json={
                    "action": "synthetic-provenance",
                    "status": "accepted",
                    "agencyId": "synthetic-agency",
                },
            )
        if path == "/api/v1/investigations" and request.method == "POST":
            body = json.loads(request.content)
            run_id = f"run-{body['transactionId']}"
            state[run_id] = body
            return httpx.Response(202, json={"runId": run_id})
        if path.startswith("/api/v1/investigations/"):
            run_id = path.rsplit("/", 1)[-1]
            body = state[run_id]
            return httpx.Response(
                200,
                json={
                    "runId": run_id,
                    "status": "completed",
                    "attempt": attempt,
                    "modelVersion": body.get("modelOverride", _MODEL),
                    "alertId": f"alert-{run_id}",
                    "retrievedRegulations": [{"citation": "31 CFR 1010.314"}],
                },
            )
        if path.startswith("/api/v1/alerts/"):
            return httpx.Response(
                200,
                json={
                    "sarDraft": {
                        "status": "draft",
                        "structured": _structured("31 CFR 1010.314"),
                        "modelId": "vllm/Qwen/Qwen2.5-7B-Instruct-AWQ",
                        "citations": [{"citation": "31 CFR 1010.314"}],
                        "costUsd": "0",
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    return httpx.MockTransport(handle)


def _run(tmp_path: Path, *, provider: str = "vllm", attempt: int = 1):
    config = load_sar_eval_config()
    with httpx.Client(
        base_url="http://127.0.0.1:18000", transport=_transport(provider=provider, attempt=attempt)
    ) as client:
        return run_e2e(
            client=client,
            config=config,
            config_bytes=DEFAULT_SAR_EVAL_CONFIG.read_bytes(),
            run_id=_RUN_ID,
            cases=4,
            concurrency=2,
            output_path=tmp_path / "e2e.json",
            model_override=_MODEL,
            sleep=lambda _seconds: None,
            now=lambda: datetime(2026, 9, 14, tzinfo=UTC),
        )


def test_e2e_proves_provider_worker_schema_and_citations(tmp_path: Path) -> None:
    report = _run(tmp_path)
    persisted = E2eApplicationReport.model_validate_json((tmp_path / "e2e.json").read_text())
    assert persisted == report
    assert report.functional_only is True
    assert report.readiness_status == "not_ready"
    assert report.llm_provider == "vllm"
    assert report.runs_submitted == report.runs_completed == 4
    assert report.runs_failed == 0
    assert report.quality.schema_valid_rate == 1
    assert report.quality.reference_validity == 1
    assert report.quality.citation_present_rate == 1
    assert all(
        item.attempt == 1 and item.writer_model_id.startswith("vllm/") for item in report.outcomes
    )


def test_e2e_namespaces_accounts_without_breaking_scenario_links() -> None:
    config = load_sar_eval_config()
    original = generate_scenarios(config, DEFAULT_SAR_EVAL_CONFIG.read_bytes()).scenarios[0]
    first = _namespaced_scenario(original, _RUN_ID, 0)
    second = _namespaced_scenario(original, _RUN_ID, 1)
    assert first == _namespaced_scenario(original, _RUN_ID, 0)
    assert {item.external_id for item in first.transactions}.isdisjoint(
        item.external_id for item in second.transactions
    )
    assert (
        len(
            {
                account
                for item in first.transactions
                for account in (item.origin_account, item.dest_account)
            }
        )
        < len(first.transactions) * 2
    )


def test_e2e_account_namespaces_remain_unique_after_phi_masking_at_max_load() -> None:
    config = load_sar_eval_config()
    original = generate_scenarios(config, DEFAULT_SAR_EVAL_CONFIG.read_bytes()).scenarios[0]
    accounts = {
        account
        for index in range(1000)
        for transaction in _namespaced_scenario(original, _RUN_ID, index).transactions
        for account in (transaction.origin_account, transaction.dest_account)
    }
    masked = {mask_identifier(account).value for account in accounts}
    assert len(masked) == len(accounts)


@pytest.mark.parametrize(("cases", "concurrency"), [(0, 1), (2, 0), (2, 3), (1001, 1)])
def test_e2e_rejects_unbounded_load(tmp_path: Path, cases: int, concurrency: int) -> None:
    config = load_sar_eval_config()
    with (
        httpx.Client(base_url="http://127.0.0.1:18000", transport=_transport()) as client,
        pytest.raises(ValueError, match="cases/concurrency"),
    ):
        run_e2e(
            client=client,
            config=config,
            config_bytes=DEFAULT_SAR_EVAL_CONFIG.read_bytes(),
            run_id=_RUN_ID,
            cases=cases,
            concurrency=concurrency,
            output_path=tmp_path / "e2e.json",
        )


def test_e2e_refuses_wrong_provider_before_ingest(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="llmProvider=vllm"):
        _run(tmp_path, provider="openrouter")


def test_e2e_retains_stable_failure_without_exception_detail(tmp_path: Path) -> None:
    report = _run(tmp_path, attempt=0)
    assert report.runs_submitted == 4
    assert report.runs_completed == 0
    assert report.runs_failed == 4
    assert {item.error_code for item in report.outcomes} == {"case_failed"}
    assert "durable worker" not in (tmp_path / "e2e.json").read_text()


def test_e2e_report_rejects_inconsistent_terminal_counts() -> None:
    failed = E2eCaseOutcome(case_id="case", status="failed", error_code="case_failed")
    with pytest.raises(ValidationError, match="terminal counts"):
        E2eApplicationReport(
            run_id=_RUN_ID,
            started_at=datetime(2026, 9, 14, tzinfo=UTC),
            completed_at=datetime(2026, 9, 14, tzinfo=UTC),
            requested_cases=1,
            concurrency=1,
            readiness_status="ready",
            llm_provider_status="ok",
            llm_provider="vllm",
            runs_submitted=0,
            runs_completed=1,
            runs_failed=0,
            total_cost_usd=0,
            quality={
                "schemaValidRate": 0,
                "referenceValidity": 0,
                "citationPresentRate": 0,
            },
            outcomes=(failed,),
        )
