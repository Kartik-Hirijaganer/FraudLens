"""Summary: Byte-level synthetic-only model-egress gate across direct, retry, and fallback calls.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- A fake OpenAI-compatible HTTP endpoint captures serialized request bodies; sockets are forbidden.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from decimal import Decimal
from typing import cast

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint
from pydantic import ValidationError
from quality_gates import production_gate
from sar_drafts import gate_passing_generation

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_live import LiveSarDrafter
from fraudlens_backend.sar.egress import (
    EgressBlockedError,
    SarModelInput,
    _matches_forbidden,
    load_egress_policy,
    project_agent_tool_result,
    project_for_model,
    sanitize_model_payload,
)
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_core import AmlRuleType
from fraudlens_core.rules.base import RuleHit
from fraudlens_llm import (
    Catalog,
    DataClass,
    GenerationParams,
    Kind,
    Lifecycle,
    LlmClient,
    LlmSettings,
    ModelCard,
    Protocol,
    ProviderConfig,
    Providers,
)
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_ml.sar import SarDraftStatus, SarInput

pytestmark = pytest.mark.quality
_LOG_PROMPT_SENTINEL = "log-prompt-sentinel"
_LOG_RESPONSE_SENTINEL = "log-response-sentinel"


def _sar_json(sar_input: object) -> str:
    """Return gate-accepted model output for THIS case, carrying the response sentinel.

    The egress assertions are about what crosses the wire and what reaches the logs, so the
    output has to be a draft the production gate actually accepts — otherwise the drafter would
    reject it and the test would prove nothing about a served SAR.
    """
    generated = gate_passing_generation(cast(SarInput, sar_input))
    return generated.model_copy(
        update={"narrative": f"{generated.narrative} {_LOG_RESPONSE_SENTINEL}"}
    ).model_dump_json(by_alias=True)


_FORBIDDEN_SENTINELS = (
    b"tenant-private-id",
    b"database-row-id",
    b"analyst@example.com",
    b"client-secret-value",
    b"Jane Synthetic",
    b"4111111111111111",
    b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZW50aW5lbCJ9.signature",
    b"postgresql://synthetic:credential@database.invalid/fraudlens",
    b"private transfer memo",
)


def _card() -> ModelCard:
    return ModelCard(
        kind=Kind.CHAT,
        context_window=4096,
        default_params=GenerationParams(max_tokens=256),
        input_price_per_million=0,
        output_price_per_million=0,
        source_url="https://provider.invalid",
        verified_at="2026-09-13",
        lifecycle=Lifecycle.GA,
        callable=True,
        pricing_basis="per_million_tokens",
    )


def _provider(*, max_retries: int) -> ProviderConfig:
    return ProviderConfig(
        protocol=Protocol.OPENAI_COMPATIBLE,
        base_url="https://provider.invalid/v1",
        api_key_env="QUALITY_FAKE_KEY",
        timeout_s=2,
        max_retries=max_retries,
        region="us",
        data_retention="0d",
        zdr_supported=True,
        training_opt_out=True,
        baa_required=False,
        allowed_data_classes=[DataClass.SYNTHETIC],
    )


def _client(
    endpoints: dict[str, tuple[CapturedOpenAiEndpoint, int]],
) -> tuple[LlmClient, list[httpx.AsyncClient]]:
    catalog = Catalog(providers={name: {"chat": _card()} for name in endpoints})
    providers = Providers(
        providers={name: _provider(max_retries=retries) for name, (_, retries) in endpoints.items()}
    )
    client = LlmClient.from_config(
        catalog,
        providers,
        LlmSettings(environment="dev", default_model=f"{next(iter(endpoints))}/chat"),
    )
    http_clients: list[httpx.AsyncClient] = []
    for name, (endpoint, retries) in endpoints.items():
        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
        adapter = OpenAiCompatibleAdapter(name, providers.providers[name])
        adapter._client = AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            max_retries=retries,
            http_client=transport_client,
        )
        client._adapters[name] = adapter
        http_clients.append(transport_client)
    return client, http_clients


def _live(client: LlmClient, *, primary: str, fallbacks: tuple[str, ...] = ()) -> LiveSarDrafter:
    return LiveSarDrafter(
        client=client,
        catalog=Catalog(providers={name: {"chat": _card()} for name in (primary, *fallbacks)}),
        prompt=SarPromptTemplate.load(),
        model=f"{primary}/chat",
        max_output_tokens=256,
        gate=production_gate(),
        budget=BudgetGuard(),
        cache=InMemorySarDraftCache(),
        fallbacks=tuple(f"{name}/chat" for name in fallbacks),
    )


async def _draft(drafter: LiveSarDrafter, sar_input) -> object:
    events = [event async for event in drafter.draft(sar_input)]
    return events[-1].result


async def _close(clients: list[httpx.AsyncClient]) -> None:
    for client in clients:
        await client.aclose()


def _no_socket(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("quality gate attempted socket IO")


@pytest.mark.asyncio
async def test_serialized_request_retry_and_fallback_contain_only_allowlisted_bytes(
    make_sar_input: Callable[..., object],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    sar_input = make_sar_input(
        agency_id="tenant-private-id",
        transaction_id="database-row-id",
        channel=_LOG_PROMPT_SENTINEL,
        rule_hits=(
            RuleHit(
                code="STRUCT",
                rule_type=AmlRuleType.STRUCTURING,
                severity="high",
                weight=Decimal("1.0"),
                reason=(
                    "Jane Synthetic analyst@example.com account=4111111111111111 "
                    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZW50aW5lbCJ9.signature "
                    "postgresql://synthetic:credential@database.invalid/fraudlens "
                    "CLIENT_SECRET=client-secret-value private transfer memo"
                ),
            ),
        ),
    )
    sar_json = _sar_json(sar_input)
    primary = CapturedOpenAiEndpoint(sar_json, failures=1)
    backup = CapturedOpenAiEndpoint(sar_json)
    client, transports = _client({"primary": (primary, 0), "backup": (backup, 0)})
    try:
        result = await _draft(_live(client, primary="primary", fallbacks=("backup",)), sar_input)
    finally:
        await _close(transports)

    assert result.status is SarDraftStatus.DRAFT
    assert len(primary.request_bodies) == 1
    assert len(backup.request_bodies) == 1
    for body in (*primary.request_bodies, *backup.request_bodies):
        assert not any(value in body for value in _FORBIDDEN_SENTINELS)
        payload = json.loads(body)
        assert payload["messages"][-1]["content"]
        assert _LOG_PROMPT_SENTINEL.encode() in body
    assert _LOG_RESPONSE_SENTINEL in result.content
    assert not any(value.decode() in caplog.text for value in _FORBIDDEN_SENTINELS)
    assert _LOG_PROMPT_SENTINEL not in caplog.text
    assert _LOG_RESPONSE_SENTINEL not in caplog.text


@pytest.mark.asyncio
async def test_sdk_retry_reuses_the_same_safe_projection(
    make_sar_input: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    sar_input = make_sar_input()
    endpoint = CapturedOpenAiEndpoint(_sar_json(sar_input), failures=1)
    client, transports = _client({"primary": (endpoint, 1)})
    try:
        result = await _draft(_live(client, primary="primary"), sar_input)
    finally:
        await _close(transports)

    assert result.status is SarDraftStatus.DRAFT
    assert len(endpoint.request_bodies) == 2
    assert endpoint.request_bodies[0] == endpoint.request_bodies[1]


@pytest.mark.asyncio
async def test_disallowed_source_and_bad_regulation_fail_before_transport(
    make_sar_input: Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    endpoint = CapturedOpenAiEndpoint(_sar_json(make_sar_input()))
    client, transports = _client({"primary": (endpoint, 0)})
    bad_citation = make_sar_input().citations[0].model_copy(update={"snippet": "altered"})
    try:
        source_result = await _draft(
            _live(client, primary="primary"), make_sar_input(source="api-upload")
        )
        citation_result = await _draft(
            _live(client, primary="primary"), make_sar_input(citations=(bad_citation,))
        )
    finally:
        await _close(transports)

    assert source_result.error_code == "egress_source_not_allowed"
    assert citation_result.error_code == "egress_regulation_not_allowed"
    assert endpoint.request_bodies == []


def test_projection_is_extra_forbid_and_data_class_cannot_be_self_declared(
    make_sar_input: Callable[..., object],
) -> None:
    projected = project_for_model(make_sar_input(), load_egress_policy())
    with pytest.raises(ValidationError, match="rawMemo"):
        SarModelInput.model_validate({**projected.model_dump(by_alias=True), "rawMemo": "x"})
    with pytest.raises(ValidationError, match="dataClass"):
        make_sar_input(dataClass="synthetic")


def test_agent_tool_results_are_allowlisted_aliased_and_remasked() -> None:
    policy = load_egress_policy()
    projected = project_agent_tool_result(
        "transaction_history",
        {
            "hits": [
                {
                    "evidenceRef": "database-row-id",
                    "title": "analyst@example.com",
                    "internalId": "database-row-id",
                }
            ],
            "debug": "CLIENT_SECRET=client-secret-value",
        },
        policy,
    )
    remasked = sanitize_model_payload(
        {"draft": "analyst@example.com", "secret": "CLIENT_SECRET=client-secret-value"}, policy
    )

    hit = projected["hits"][0]
    assert hit["evidenceRef"].startswith("case-evidence-")
    assert hit["title"] == "[REDACTED_EMAIL]"
    assert "internalId" not in hit
    assert "debug" not in projected
    assert "analyst@example.com" not in str(remasked)


def test_account_detector_blocks_standalone_numbers_without_matching_hashes_or_decimals() -> None:
    policy = load_egress_policy()
    digest = "123456789012abcdef123456789012abcdef123456789012abcdef123456789012"
    sanitized = sanitize_model_payload(
        {
            "standalone": "1234567890123456",
            "digest": digest,
            "decimal": "0.1234567890123456",
        },
        policy,
    )

    assert sanitized["standalone"] != "1234567890123456"
    assert "REDACTED" in sanitized["standalone"]
    assert _matches_forbidden("1234567890123456", policy)
    assert not _matches_forbidden(digest, policy)
    assert not _matches_forbidden("0.1234567890123456", policy)


def test_project_for_model_raises_stable_source_code(make_sar_input: Callable[..., object]) -> None:
    with pytest.raises(EgressBlockedError, match="egress_source_not_allowed") as error:
        project_for_model(make_sar_input(source="unknown"), load_egress_policy())
    assert error.value.code == "egress_source_not_allowed"
