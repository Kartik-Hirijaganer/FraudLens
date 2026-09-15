"""Summary: Functional vLLM application-pass runner through the real API and durable worker.

Key classes:
- E2eCaseOutcome: PHI-free terminal evidence for one synthetic application case.
- E2eQuality: aggregate persisted-draft validation metrics.
- E2eApplicationReport: hashable functional evidence for the complete application pass.

Key functions:
- run_e2e: verify the active provider, execute bounded synthetic cases, and write a report.

Notes:
- This proves API -> durable worker -> live vLLM wiring; its timings are never benchmark latency.
- Raw transactions and SAR narratives are deliberately omitted from the persisted report.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from fraudlens_ml.sar import SarDraftContent
from lib.sar_eval.config import SarEvalConfig
from lib.sar_eval.runner_transport import ingest, poll, response_body
from lib.sar_eval.scenarios import (
    SarEvalScenario,
    SyntheticTransaction,
    generate_scenarios,
)
from lib.study.artifacts import atomic_write_model

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    alias_generator=to_camel,
    populate_by_name=True,
    protected_namespaces=(),
)
_HTTP_READY = frozenset({200, 503})
_PROVIDER_CHECK = "llmProvider"
_MAX_CASES = 1000
_MAX_CONCURRENCY = 64
_ACCOUNT_SLOTS_PER_CASE = 16


class E2eCaseOutcome(BaseModel):
    """One synthetic case's terminal, PHI-free application-path evidence."""

    model_config = _MODEL_CONFIG

    case_id: str = Field(..., min_length=1, description="Namespaced synthetic case id.")
    status: Literal["completed", "failed"] = Field(..., description="Functional outcome.")
    run_id: str | None = Field(default=None, description="Persisted investigation run id.")
    error_code: Literal["case_failed"] | None = Field(
        default=None, description="Stable failure code without exception or payload detail."
    )
    attempt: int = Field(default=0, ge=0, description="Durable worker claim attempt observed.")
    model_version: str | None = Field(
        default=None, description="Scoring-model version recorded by the application."
    )
    writer_model_id: str | None = Field(
        default=None, description="Persisted SAR writer provider/model identity."
    )
    schema_valid: bool = Field(default=False, description="Persisted SAR matches its schema.")
    reference_validity: float = Field(
        default=0, ge=0, le=1, description="Draft citation ids present in offered evidence."
    )
    citation_present: bool = Field(
        default=False, description="The persisted draft contains at least one citation."
    )
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0, description="Persisted draft cost.")

    @model_validator(mode="after")
    def _terminal_shape(self) -> E2eCaseOutcome:
        """Keep successful and failed outcome fields internally consistent."""
        if self.status == "completed" and (
            self.run_id is None
            or self.error_code is not None
            or self.attempt < 1
            or not self.schema_valid
            or self.writer_model_id is None
        ):
            raise ValueError("completed e2e outcomes require durable vLLM draft evidence")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed e2e outcomes require a stable error code")
        return self


class E2eQuality(BaseModel):
    """Aggregate quality checks over persisted application drafts."""

    model_config = _MODEL_CONFIG

    schema_valid_rate: float = Field(..., ge=0, le=1, description="Valid draft fraction.")
    reference_validity: float = Field(..., ge=0, le=1, description="Mean citation validity.")
    citation_present_rate: float = Field(..., ge=0, le=1, description="Cited draft fraction.")


class E2eApplicationReport(BaseModel):
    """Functional evidence for the real API/worker/vLLM application path."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., pattern=r"^vllm-e2e-[0-9a-f]{16}$", description="Pass identity.")
    functional_only: Literal[True] = Field(
        default=True, description="Disallows interpreting this pass as a latency benchmark."
    )
    started_at: datetime = Field(..., description="UTC pass start.")
    completed_at: datetime = Field(..., description="UTC pass completion.")
    requested_cases: int = Field(..., gt=0, le=1000, description="Requested synthetic cases.")
    concurrency: int = Field(..., gt=0, le=64, description="Maximum in-flight application cases.")
    readiness_status: str = Field(..., min_length=1, description="Aggregate /readyz status.")
    llm_provider_status: Literal["ok"] = Field(..., description="Provider readiness state.")
    llm_provider: Literal["vllm"] = Field(..., description="Provider selected by the app.")
    model_override: str | None = Field(
        default=None, description="Optional scoring-model version requested for every case."
    )
    runs_submitted: int = Field(..., ge=0, description="Investigations accepted by the API.")
    runs_completed: int = Field(..., ge=0, description="Investigations completed by workers.")
    runs_failed: int = Field(..., ge=0, description="Cases without a valid completed draft.")
    total_cost_usd: Decimal = Field(..., ge=0, description="Sum of persisted draft costs.")
    quality: E2eQuality = Field(..., description="Persisted-draft quality metrics.")
    outcomes: tuple[E2eCaseOutcome, ...] = Field(..., description="Per-case PHI-free outcomes.")

    @model_validator(mode="after")
    def _reconcile(self) -> E2eApplicationReport:
        """Reconcile declared counts with the exact outcome set."""
        completed = sum(item.status == "completed" for item in self.outcomes)
        submitted = sum(item.run_id is not None for item in self.outcomes)
        if len(self.outcomes) != self.requested_cases:
            raise ValueError("e2e outcomes must equal requestedCases")
        if self.runs_completed != completed or self.runs_failed != len(self.outcomes) - completed:
            raise ValueError("e2e terminal counts do not reconcile")
        if self.runs_submitted != submitted or len({item.case_id for item in self.outcomes}) != len(
            self.outcomes
        ):
            raise ValueError("e2e submitted count or case identity does not reconcile")
        return self


def _namespaced_scenario(
    scenario: SarEvalScenario, execution_id: str, index: int
) -> SarEvalScenario:
    """Clone one calibrated scenario under short, deterministic synthetic identifiers."""
    digest = hashlib.sha256(f"{execution_id}:{index}".encode()).hexdigest()[:12]
    accounts = sorted(
        {
            account
            for item in scenario.transactions
            for account in (item.origin_account, item.dest_account)
        }
    )
    account_map = {
        account: (f"SYNTH-E2E-A-{digest}-{index * _ACCOUNT_SLOTS_PER_CASE + position:04x}")
        for position, account in enumerate(accounts)
    }
    transaction_map = {
        item.external_id: f"E2E-{digest}-{position:02d}"
        for position, item in enumerate(scenario.transactions)
    }
    transactions = tuple(
        SyntheticTransaction(
            external_id=transaction_map[item.external_id],
            amount=item.amount,
            currency=item.currency,
            occurred_at=item.occurred_at,
            origin_account=account_map[item.origin_account],
            dest_account=account_map[item.dest_account],
            channel=item.channel,
            country=item.country,
            features=item.features,
        )
        for item in scenario.transactions
    )
    return SarEvalScenario(
        scenario_id=f"{scenario.scenario_id}-{index:03d}",
        typology=scenario.typology,
        variant=scenario.variant,
        subject_external_id=transaction_map[scenario.subject_external_id],
        transactions=transactions,
        expected_citation_ids=scenario.expected_citation_ids,
    )


def _readiness(client: httpx.Client) -> tuple[str, Literal["ok"], Literal["vllm"]]:
    """Require /readyz to prove that the application selected a reachable vLLM provider."""
    response = client.get("/readyz")
    if response.status_code not in _HTTP_READY:
        raise RuntimeError("application readiness endpoint returned an unexpected status")
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("checks"), list):
        raise RuntimeError("application readiness response is malformed")
    checks = [
        item
        for item in body["checks"]
        if isinstance(item, dict) and item.get("name") == _PROVIDER_CHECK
    ]
    if len(checks) != 1 or checks[0].get("status") != "ok" or checks[0].get("detail") != "vllm":
        raise RuntimeError("application readiness did not report llmProvider=vllm")
    status = body.get("status")
    if not isinstance(status, str) or not status:
        raise RuntimeError("application readiness response lacks aggregate status")
    return status, "ok", "vllm"


def _citation_ids(value: object) -> tuple[str, ...]:
    """Extract ordered citation ids from an API list while rejecting malformed rows."""
    if not isinstance(value, list):
        raise RuntimeError("application citation evidence is malformed")
    ids: list[str] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("citation"), str):
            raise RuntimeError("application citation evidence is malformed")
        ids.append(item["citation"])
    return tuple(dict.fromkeys(ids))


def _case_outcome(  # noqa: PLR0913 - explicit transport/time inputs keep the case testable.
    client: httpx.Client,
    scenario: SarEvalScenario,
    config: SarEvalConfig,
    *,
    model_override: str | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> E2eCaseOutcome:
    """Run one calibrated synthetic case through ingest, queue, worker, and persisted SAR."""
    run_id: str | None = None
    try:
        transaction_id = ingest(client, scenario)
        response_body(
            client.post(f"/api/v1/dev/transactions/{transaction_id}/synthetic-provenance"),
            200,
        )
        payload: dict[str, object] = {
            "transactionId": transaction_id,
            "workflowMode": "single_writer",
        }
        if model_override is not None:
            payload["modelOverride"] = model_override
        response = client.post(
            "/api/v1/investigations",
            json=payload,
            headers={"Idempotency-Key": hashlib.sha256(scenario.scenario_id.encode()).hexdigest()},
        )
        run_id = str(response_body(response, 202)["runId"])
        snapshot = poll(
            client,
            run_id,
            timeout_s=config.api.run_timeout_s,
            poll_interval_s=config.api.poll_interval_s,
            clock=clock,
            sleep=sleep,
        )
        attempt = snapshot.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise RuntimeError("application pass did not traverse a durable worker claim")
        if model_override is not None and snapshot.get("modelVersion") != model_override:
            raise RuntimeError("application pass did not use the requested scoring model")
        alert_id = snapshot.get("alertId")
        if not isinstance(alert_id, str) or not alert_id:
            raise RuntimeError("application pass did not create a reviewable alert")
        detail = response_body(client.get(f"/api/v1/alerts/{alert_id}"), 200)
        draft = detail.get("sarDraft")
        if not isinstance(draft, dict) or draft.get("status") != "draft":
            raise RuntimeError("completed application case lacks a persisted SAR draft")
        SarDraftContent.model_validate(draft.get("structured"))
        writer = draft.get("modelId")
        if not isinstance(writer, str) or not writer.startswith("vllm/"):
            raise RuntimeError("persisted SAR was not written by the vLLM provider")
        produced = _citation_ids(draft.get("citations"))
        offered = set(_citation_ids(snapshot.get("retrievedRegulations")))
        validity = sum(item in offered for item in produced) / len(produced) if produced else 0.0
        return E2eCaseOutcome(
            case_id=scenario.scenario_id,
            status="completed",
            run_id=run_id,
            attempt=attempt,
            model_version=str(snapshot.get("modelVersion")),
            writer_model_id=writer,
            schema_valid=True,
            reference_validity=validity,
            citation_present=bool(produced),
            cost_usd=Decimal(str(draft.get("costUsd", "0"))),
        )
    except (
        Exception
    ):  # failures persist only a stable code; payloads/exceptions never enter evidence
        return E2eCaseOutcome(
            case_id=scenario.scenario_id,
            status="failed",
            run_id=run_id,
            error_code="case_failed",
        )


def _quality(outcomes: tuple[E2eCaseOutcome, ...]) -> E2eQuality:
    """Aggregate quality with failed cases contributing zero to every rate."""
    count = len(outcomes)
    return E2eQuality(
        schema_valid_rate=sum(item.schema_valid for item in outcomes) / count,
        reference_validity=sum(item.reference_validity for item in outcomes) / count,
        citation_present_rate=sum(item.citation_present for item in outcomes) / count,
    )


def run_e2e(  # noqa: PLR0913 - explicit transport/time inputs keep paid execution testable.
    *,
    client: httpx.Client,
    config: SarEvalConfig,
    config_bytes: bytes,
    run_id: str,
    cases: int,
    concurrency: int,
    output_path: Path,
    model_override: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> E2eApplicationReport:
    """Execute and persist a bounded functional API/worker/vLLM pass."""
    if not 1 <= cases <= _MAX_CASES or not 1 <= concurrency <= min(cases, _MAX_CONCURRENCY):
        raise ValueError("e2e cases/concurrency must satisfy 1 <= concurrency <= cases <= 1000")
    started = now()
    readiness_status, provider_status, provider = _readiness(client)
    base = generate_scenarios(config, config_bytes).scenarios
    selected = tuple(
        _namespaced_scenario(base[index % len(base)], run_id, index) for index in range(cases)
    )

    def execute(scenario: SarEvalScenario) -> E2eCaseOutcome:
        return _case_outcome(
            client,
            scenario,
            config,
            model_override=model_override,
            clock=clock,
            sleep=sleep,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcomes = tuple(executor.map(execute, selected))
    completed = sum(item.status == "completed" for item in outcomes)
    report = E2eApplicationReport(
        run_id=run_id,
        started_at=started,
        completed_at=now(),
        requested_cases=cases,
        concurrency=concurrency,
        readiness_status=readiness_status,
        llm_provider_status=provider_status,
        llm_provider=provider,
        model_override=model_override,
        runs_submitted=sum(item.run_id is not None for item in outcomes),
        runs_completed=completed,
        runs_failed=len(outcomes) - completed,
        total_cost_usd=sum((item.cost_usd for item in outcomes), Decimal("0")),
        quality=_quality(outcomes),
        outcomes=outcomes,
    )
    atomic_write_model(output_path, report)
    return report
