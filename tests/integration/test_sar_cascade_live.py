"""Summary: The real AWQ→BF16→external cascade over three distinct in-memory connections.

Key classes:
- (none)

Key functions:
- test_a_twice_rejected_case_is_served_by_the_external_tier: the full escalation path.
- test_no_rejected_tier_text_reaches_any_streamed_event: buffering holds end to end.
- test_no_forbidden_sentinel_reaches_any_tier_or_any_log: egress holds on all three tiers.

Notes:
- This is the closest thing to the shipped path that runs in CI: real `LiveSarDrafter`s built by
  the real factory from the committed `awq-bf16-external` profile, real prompts, real constrained
  decoding, real guardrails, real gate — with only the three HTTP endpoints replaced by
  `CapturedOpenAiEndpoint`, which captures exact request BYTES. No socket, GPU, or credential.
- The escalation itself is the subject. Three properties are asserted that only a real three-tier
  run can show: the tier-2 and tier-3 payloads carry byte-identical projected messages to tier-1
  (escalating must not smuggle anything extra to a second provider, least of all a hosted one),
  the self-hosted tiers emit a CLOSED citation enum so fabrication is structurally impossible,
  and the hosted tier carries zero-data-retention and a data-collection denial on the wire.
- Containment is asserted in both directions: no rejected tier's narrative reaches any streamed
  event, and no forbidden sentinel reaches any request body or any log record on any tier.
"""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Callable

import httpx
import pytest
from openai import AsyncOpenAI
from openai_compatible_fake import CapturedOpenAiEndpoint
from sar_drafts import FABRICATED_CITATION_ID, gate_passing_generation, gate_passing_json

from fraudlens_backend.sar.budget import BudgetGuard
from fraudlens_backend.sar.cache import InMemorySarDraftCache
from fraudlens_backend.sar.drafter_gated import QualityGatedSarDrafter
from fraudlens_backend.sar.factory import build_sar_drafter, load_sar_llm_config
from fraudlens_backend.settings import AppSettings, _config_anchored, find_config_dir
from fraudlens_llm import LlmClient, LlmSettings, load_catalog, load_providers
from fraudlens_llm.adapters.openai_compatible import OpenAiCompatibleAdapter
from fraudlens_ml.sar import SarDraftStatus, SarEventType, SarInput

_PROFILE = "awq-bf16-external"
_REJECTED_MARKER = "rejected-tier-narrative-marker"
_FORBIDDEN_SENTINELS = (
    b"tenant-private-id",
    b"database-row-id",
    b"analyst@example.com",
    b"4111111111111111",
    b"private transfer memo",
)


@pytest.fixture(autouse=True)
def _deny_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """AD-3.1: this suite is offline — a real connection attempt is a failure, not a slow test."""

    def _no_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline suite attempted socket IO")

    monkeypatch.setattr(socket.socket, "connect", _no_socket)


def _settings() -> LlmSettings:
    """Bind the committed catalog and provider registries in a non-prod environment."""
    config_dir = find_config_dir()
    return LlmSettings(
        catalog_path=config_dir / "llm" / "catalog.yml",
        providers_path=config_dir / "llm" / "providers.yml",
        environment="dev",
    )


def _rejected_json(sar_input: SarInput) -> str:
    """Return output the gate rejects, marked so its text is traceable if it ever escapes."""
    generated = gate_passing_generation(sar_input)
    return generated.model_copy(
        update={
            "citation_ids": (*generated.citation_ids, FABRICATED_CITATION_ID),
            "narrative": f"{generated.narrative} {_REJECTED_MARKER}",
        }
    ).model_dump_json(by_alias=True)


def _bind(
    client: LlmClient, model: str, connection: str, endpoint: CapturedOpenAiEndpoint
) -> httpx.AsyncClient:
    """Point one named connection's adapter at its own in-memory transport."""
    adapter = client._adapter_for(client._resolve_model(model, connection))
    assert isinstance(adapter, OpenAiCompatibleAdapter)
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    adapter._client = AsyncOpenAI(
        api_key="synthetic-test-value",
        base_url=f"http://{connection}.test/v1",
        http_client=transport_client,
        max_retries=0,
    )
    return transport_client


class _Cascade:
    """One wired three-connection cascade plus the endpoints that captured its requests."""

    def __init__(self, sar_input: SarInput, responses: tuple[str, str, str]) -> None:
        """Build the committed profile's drafter with one captured endpoint per stage."""
        settings = _settings()
        catalog = load_catalog(settings.catalog_path)
        providers = load_providers(settings.providers_path)
        stages = load_sar_llm_config(_config_anchored("llm/sar-vllm.yml")).stages(_PROFILE)
        client = LlmClient.from_config(catalog, providers, settings)
        self.stages = stages
        self.endpoints = {
            stage.name: CapturedOpenAiEndpoint(response)
            for stage, response in zip(stages, responses, strict=True)
        }
        self._transports = [
            _bind(client, stage.model, stage.connection, self.endpoints[stage.name])
            for stage in stages
        ]
        self.drafter = build_sar_drafter(
            AppSettings(
                environment="dev",
                llm_mode="live",
                sar_config_file="llm/sar-vllm.yml",
                sar_profile=_PROFILE,
            ),
            client=client,
            catalog=catalog,
            budget=BudgetGuard(),
            cache=InMemorySarDraftCache(),
        )
        assert isinstance(self.drafter, QualityGatedSarDrafter)

    async def run(self, sar_input: SarInput) -> list:
        """Drain the cascade and close every transport."""
        try:
            return [event async for event in self.drafter.draft(sar_input)]
        finally:
            for transport in self._transports:
                await transport.aclose()

    def body(self, stage: str) -> dict:
        """Return one stage's single captured request body, decoded."""
        bodies = self.endpoints[stage].request_bodies
        assert len(bodies) == 1, f"stage {stage} made {len(bodies)} requests"
        return json.loads(bodies[0])

    def raw_bodies(self) -> list[bytes]:
        """Return every captured request body across every stage."""
        return [body for endpoint in self.endpoints.values() for body in endpoint.request_bodies]


@pytest.mark.asyncio
async def test_a_twice_rejected_case_is_served_by_the_external_tier(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """Two fabricating self-hosted tiers escalate to the hosted one, which serves the draft."""
    sar_input = make_sar_input()
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )

    events = await cascade.run(sar_input)
    result = events[-1].result

    assert result is not None
    assert result.status is SarDraftStatus.DRAFT
    assert result.escalation_tier == 2
    assert result.escalated_from == ("awq", "bf16")
    assert [event.stage.stage for event in events if event.type is SarEventType.ESCALATED] == [
        "bf16",
        "external",
    ]
    assert [len(endpoint.request_bodies) for endpoint in cascade.endpoints.values()] == [1, 1, 1]
    assert result.structured is not None
    assert FABRICATED_CITATION_ID not in result.structured.cited_regulations


@pytest.mark.asyncio
async def test_every_tier_is_sent_byte_identical_projected_messages(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """Escalating must not enlarge the payload: tier 2 and 3 see exactly what tier 1 saw."""
    sar_input = make_sar_input()
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )

    await cascade.run(sar_input)
    messages = [json.dumps(cascade.body(stage.name)["messages"]) for stage in cascade.stages]

    assert len(set(messages)) == 1


@pytest.mark.asyncio
async def test_the_self_hosted_tiers_emit_a_closed_citation_enum(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """Constrained decoding offers exactly the ids the case has, so fabrication is impossible."""
    sar_input = make_sar_input()
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )
    offered = [citation.citation for citation in sar_input.citations]

    await cascade.run(sar_input)

    for stage in cascade.stages:
        body = cascade.body(stage.name)
        assert body["response_format"]["type"] == "json_schema", stage.name
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["citationIds"]["items"]["enum"] == offered, stage.name
        assert "evidenceRefs" not in json.dumps(schema), stage.name
        assert "assertedFacts" not in json.dumps(schema), stage.name


@pytest.mark.asyncio
async def test_the_hosted_tier_carries_zero_data_retention_on_the_wire(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """The ZDR posture is a request option on every hosted call, not a claim in a YAML file."""
    sar_input = make_sar_input()
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )

    await cascade.run(sar_input)
    external = cascade.body("external")

    assert external["provider"] == {"zdr": True, "data_collection": "deny"}
    for stage in ("awq", "bf16"):
        assert "provider" not in cascade.body(stage)


@pytest.mark.asyncio
async def test_no_rejected_tier_text_reaches_any_streamed_event(
    make_sar_input: Callable[..., SarInput],
) -> None:
    """A rejected draft is not a disclosable artifact: its narrative reaches no client frame."""
    sar_input = make_sar_input()
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )

    events = await cascade.run(sar_input)
    frames = "".join(event.model_dump_json(by_alias=True) for event in events)
    result = events[-1].result

    assert _REJECTED_MARKER not in frames
    assert "".join(event.token or "" for event in events) == result.content
    assert FABRICATED_CITATION_ID not in result.content
    assert FABRICATED_CITATION_ID not in "".join(event.token or "" for event in events)
    # The id survives in ONE place only: the per-attempt audit verdict, which is what makes the
    # escalation reconstructable. Reason codes and fabricated ids are PHI-free findings, not the
    # rejected draft; the narrative that carried them is gone.
    rejected_attempts = [attempt for attempt in result.attempts if attempt.stage != "external"]
    assert len(rejected_attempts) == 2
    assert all(
        FABRICATED_CITATION_ID in attempt.quality.fabricated_citation_ids
        for attempt in rejected_attempts
    )


@pytest.mark.asyncio
async def test_no_forbidden_sentinel_reaches_any_tier_or_any_log(
    make_sar_input: Callable[..., SarInput],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Egress holds on all three tiers, including the one that leaves the self-hosted GPU."""
    caplog.set_level(logging.DEBUG)
    sar_input = make_sar_input(
        agency_id="tenant-private-id",
        transaction_id="database-row-id",
        channel="wire analyst@example.com account=4111111111111111 private transfer memo",
    )
    cascade = _Cascade(
        sar_input,
        (_rejected_json(sar_input), _rejected_json(sar_input), _sar_json(sar_input)),
    )

    await cascade.run(sar_input)

    bodies = cascade.raw_bodies()
    assert len(bodies) == 3
    for sentinel in _FORBIDDEN_SENTINELS:
        assert all(sentinel not in body for body in bodies), sentinel
        assert sentinel.decode() not in caplog.text, sentinel


def _sar_json(sar_input: SarInput) -> str:
    """Return the gate-accepted output the hosted tier answers with."""
    return gate_passing_json(sar_input)
