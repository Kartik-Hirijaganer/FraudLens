"""Authenticated production smoke (release 0.4.0 Phase 4) — the layer the ops probes cannot reach.

`test_smoke.py` proves the process is up and `test_portfolio_demo_smoke.py` proves the pinned story
is served. Neither proves that a REAL signed-in session works: that Supabase JWT verification is
configured against the right project, that the server-owned role actually separates a permitted
analyst from a read-only auditor, that a live run reaches a terminal state with citations grounded
in the retrieved corpus, or that the event stream survives an intermediary. Those are the claims a
recruiter-facing deployment makes, so they are the claims this file asserts.

Every request here targets `/api/v1/...` and nothing else. That is deliberate: the same selection
runs twice — once against the staged Container Apps revision before promotion, and once against
`https://fraud-lens-amber.vercel.app`, where only `/api/*` is proxied to the gateway and an
unprefixed ops path would be answered by the SPA shell. A test that hit `/healthz` would silently
pass against `index.html` and prove nothing about the proxy.

Environment:

* `SMOKE_BASE_URL` — the target (the staged revision, or the Vercel origin for the proxy pass).
* `PORTFOLIO_DEMO_SMOKE_ENABLED` — `true` only where the story is actually bootstrapped.
* `SMOKE_AUTH_TOKEN` / `SMOKE_AUDITOR_TOKEN` — short-lived persona tokens minted by
  `scripts/smoke_auth_token.py`. A missing token FAILS rather than skips: a silent skip would let
  a deployment whose authentication is broken promote itself.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

from fraudlens_backend.portfolio_demo import PortfolioDemoConfig, load_portfolio_demo_config

pytestmark = pytest.mark.smoke

BASE_URL = os.environ.get("SMOKE_BASE_URL", "")
STORY_ENABLED = os.environ.get("PORTFOLIO_DEMO_SMOKE_ENABLED", "").strip().lower() == "true"
ANALYST_TOKEN = os.environ.get("SMOKE_AUTH_TOKEN", "").strip()
AUDITOR_TOKEN = os.environ.get("SMOKE_AUDITOR_TOKEN", "").strip()

_API = "/api/v1"
_TIMEOUT = 30.0
_OK = 200
_ACCEPTED = 202
_FORBIDDEN = 403
_NOT_FOUND = 404
_ENVELOPE_FIELDS = {"code", "message", "requestId"}
_TERMINAL_STATES = {"completed", "failed"}
_RUN_DEADLINE_SECONDS = 240.0
_RUN_POLL_SECONDS = 5.0
_STREAM_FRAME_DEADLINE_SECONDS = 60.0

requires_live_story = pytest.mark.skipif(
    not (BASE_URL and STORY_ENABLED),
    reason="SMOKE_BASE_URL and PORTFOLIO_DEMO_SMOKE_ENABLED=true are required",
)


@pytest.fixture(scope="module")
def story() -> PortfolioDemoConfig:
    """Return the configured story so every identity assertion reads from one source."""
    return load_portfolio_demo_config()


def _client(token: str, persona: str) -> httpx.Client:
    """Return a client bound to one persona's token, failing loudly when it was never minted."""
    if not token:
        pytest.fail(
            f"no token for the '{persona}' persona. Mint one with "
            "`scripts/smoke_auth_token.py --export <persona>=<ENV>`; an authenticated smoke "
            "that skips proves nothing about production authentication."
        )
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )


@pytest.fixture(scope="module")
def analyst() -> Iterator[httpx.Client]:
    """Yield a signed-in analyst — the persona permitted to run an investigation."""
    with _client(ANALYST_TOKEN, "analyst") as client:
        yield client


@pytest.fixture(scope="module")
def auditor() -> Iterator[httpx.Client]:
    """Yield a signed-in auditor — the read-only persona every mutation must refuse."""
    with _client(AUDITOR_TOKEN, "auditor") as client:
        yield client


def _json(response: httpx.Response) -> dict[str, object]:
    """Return a successful response's JSON body, asserting the status first."""
    assert response.status_code == _OK, f"{response.request.url} -> {response.status_code}"
    body = response.json()
    assert isinstance(body, dict)
    return body


def _envelope(response: httpx.Response, expected_status: int) -> dict[str, object]:
    """Assert a refusal carries the FraudLens error envelope and return it."""
    assert response.status_code == expected_status, (
        f"{response.request.url} -> {response.status_code}"
    )
    body = response.json()
    assert isinstance(body, dict)
    assert set(body) >= _ENVELOPE_FIELDS, body.keys()
    # A raw exception class or a traceback in `message` would be the envelope's whole point missed.
    assert "Traceback" not in str(body["message"])
    return body


# --- identity: the verified session reports the server-owned role and tenant ----------------


@requires_live_story
def test_me_reports_the_configured_analyst_identity(
    analyst: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """A real Supabase session resolves to the configured persona, role, and tenant."""
    body = _json(analyst.get(f"{_API}/me"))
    persona = story.persona("analyst")
    assert body["email"] == persona.email
    assert body["displayName"] == persona.display_name
    assert body["role"] == persona.role.value
    assert body["agencyId"] == str(story.agency.id)


@requires_live_story
def test_me_reports_the_auditor_role_on_the_same_tenant(
    auditor: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """The role is the SERVER's, not the client's: two sessions differ only in what they may do."""
    body = _json(auditor.get(f"{_API}/me"))
    persona = story.persona("auditor")
    assert body["email"] == persona.email
    assert body["role"] == persona.role.value
    assert body["agencyId"] == str(story.agency.id)


# --- authorization: read-only means read-only, and a foreign tenant is invisible -------------


@requires_live_story
def test_an_auditor_may_read_the_dashboard(auditor: httpx.Client) -> None:
    """The refusals below must be about the ACTION, not about a broken auditor session."""
    assert "transactions" in _json(auditor.get(f"{_API}/dashboard/metrics"))


@requires_live_story
def test_an_auditor_cannot_start_an_investigation(auditor: httpx.Client) -> None:
    """`start_investigation` is not an auditor permission, so the write fails closed with 403."""
    response = auditor.post(f"{_API}/investigations", json={"transactionId": str(uuid.uuid4())})
    _envelope(response, _FORBIDDEN)


@requires_live_story
def test_an_auditor_cannot_act_on_an_alert(auditor: httpx.Client) -> None:
    """Permission is checked BEFORE the alert is resolved, so an unknown id still returns 403."""
    response = auditor.post(f"{_API}/alerts/{uuid.uuid4()}/actions", json={"action": "dismiss"})
    _envelope(response, _FORBIDDEN)


@requires_live_story
def test_a_foreign_tenant_is_refused_without_confirming_it_exists(analyst: httpx.Client) -> None:
    """A path tenant that contradicts the JWT claim is a 403 that echoes neither id."""
    stranger = uuid.uuid4()
    body = _envelope(analyst.get(f"{_API}/agencies/{stranger}"), _FORBIDDEN)
    assert str(stranger) not in str(body["message"])


@requires_live_story
def test_a_foreign_resource_id_is_a_safe_404(analyst: httpx.Client) -> None:
    """A row outside the tenant is indistinguishable from one that never existed.

    The portfolio-demo suite asserts this against the gateway directly; it is repeated here under
    a verified analyst JWT because this selection is also the one that runs through the Vercel
    proxy, where tenant safety must hold identically.
    """
    stranger = uuid.uuid4()
    body = _envelope(analyst.get(f"{_API}/alerts/{stranger}"), _NOT_FOUND)
    assert str(stranger) not in str(body["message"])


# --- the reads the recruiter-facing screens are built on --------------------------------------


def _pick_transaction(client: httpx.Client, *, bands: tuple[str, ...] = ()) -> str:
    """Return one investigable transaction id, preferring the risk bands asked for.

    Band matters for what an investigation produces. The story places its five UNSCORED rows
    closest to the anchor, so the newest transaction is the one least likely to draft a SAR or
    retrieve a regulation — picking it made the citation assertion unsatisfiable by construction.
    """
    for band in bands:
        body = _json(client.get(f"{_API}/transactions", params={"riskBand": band, "limit": 1}))
        matches = body["transactions"]
        assert isinstance(matches, list)
        if matches and isinstance(matches[0], dict):
            return str(matches[0]["transactionId"])
    body = _json(client.get(f"{_API}/transactions", params={"limit": 1}))
    transactions = body["transactions"]
    assert isinstance(transactions, list) and transactions, "the demo tenant serves no transaction"
    first = transactions[0]
    assert isinstance(first, dict)
    assert not bands, f"the demo tenant serves no transaction in any of {bands}"
    return str(first["transactionId"])


@requires_live_story
def test_a_transaction_resolves_to_its_own_detail_with_accounts_masked(
    analyst: httpx.Client, story: PortfolioDemoConfig
) -> None:
    """The detail read behind every deep link returns the same row, on the caller's tenant."""
    transaction_id = _pick_transaction(analyst)
    detail = _json(analyst.get(f"{_API}/transactions/{transaction_id}"))
    assert detail["transactionId"] == transaction_id
    assert detail["agencyId"] == str(story.agency.id)
    # Persistence masks account identifiers to their last four characters; an unmasked value
    # here would be PHI-shaped data crossing the API surface. The shape is derived rather than
    # compared against the configured account, which never leaves the backend.
    for field in ("originAccount", "destAccount"):
        account = str(detail[field])
        assert account[:-4] == "*" * (len(account) - 4), field


@requires_live_story
def test_an_open_alert_resolves_to_its_own_detail_view(analyst: httpx.Client) -> None:
    """The alert queue's detail read — the screen an analyst actually works from."""
    listed = _json(analyst.get(f"{_API}/alerts", params={"status": "open"}))
    alerts = listed["alerts"]
    assert isinstance(alerts, list) and alerts, "the pinned story leaves no open alert to view"
    first = alerts[0]
    assert isinstance(first, dict)
    detail = _json(analyst.get(f"{_API}/alerts/{first['alertId']}"))
    assert detail["alert"]["alertId"] == first["alertId"]
    assert isinstance(detail["actions"], list)


# --- the permitted path: one live investigation, grounded in the retrieved corpus ------------


def _await_terminal(client: httpx.Client, run_id: str) -> dict[str, object]:
    """Poll the authoritative snapshot until the run is terminal, or fail with its last state."""
    deadline = time.monotonic() + _RUN_DEADLINE_SECONDS
    snapshot: dict[str, object] = {}
    while time.monotonic() < deadline:
        snapshot = _json(client.get(f"{_API}/investigations/{run_id}"))
        if str(snapshot["status"]) in _TERMINAL_STATES:
            return snapshot
        time.sleep(_RUN_POLL_SECONDS)
    pytest.fail(
        f"the run did not reach a terminal state within {_RUN_DEADLINE_SECONDS:.0f}s "
        f"(last status: {snapshot.get('status')!r}, error: {snapshot.get('errorCode')!r})"
    )


@pytest.fixture(scope="module")
def completed_run(analyst: httpx.Client) -> str:
    """Start one investigation as the analyst and return its id once it is terminal."""
    if not (BASE_URL and STORY_ENABLED):
        pytest.skip("SMOKE_BASE_URL and PORTFOLIO_DEMO_SMOKE_ENABLED=true are required")
    # A critical or high row is the one the story guarantees produces an alert, a SAR draft, and
    # the regulatory retrieval the citations are grounded against.
    transaction_id = _pick_transaction(analyst, bands=("critical", "high"))
    started = analyst.post(f"{_API}/investigations", json={"transactionId": transaction_id})
    assert started.status_code == _ACCEPTED, started.text
    return str(started.json()["runId"])


@requires_live_story
def test_the_analyst_investigation_completes(analyst: httpx.Client, completed_run: str) -> None:
    """A permitted investigation runs end to end against the deployed pipeline."""
    snapshot = _await_terminal(analyst, completed_run)
    assert snapshot["status"] == "completed", (
        f"the run ended {snapshot['status']!r} (error: {snapshot.get('errorCode')!r})"
    )
    assert snapshot["riskBand"], "a completed run must carry a risk band"


@requires_live_story
def test_every_citation_resolves_to_retrieved_regulatory_evidence(
    analyst: httpx.Client, completed_run: str
) -> None:
    """The baked FinCEN/BSA index is live and no citation points outside what was retrieved."""
    snapshot = _await_terminal(analyst, completed_run)
    retrieved = snapshot["retrievedRegulations"]
    assert isinstance(retrieved, list) and retrieved, (
        "the run persisted no regulatory retrieval. `/readyz` proves the baked index is present "
        "and non-empty (index_status only reports ready above zero chunks), so suspect the run "
        "itself — a band that drafts no SAR, or a retrieval step that failed silently."
    )
    available = {str(item["citation"]) for item in retrieved if isinstance(item, dict)}
    citations = snapshot["citations"]
    assert isinstance(citations, list)
    assert citations, "the drafted SAR offered no citation to check"
    ungrounded = sorted(
        str(citation["citation"])
        for citation in citations
        if isinstance(citation, dict) and str(citation["citation"]) not in available
    )
    assert not ungrounded, f"citations resolve to no retrieved evidence: {ungrounded}"


# --- the event stream survives whatever sits in front of the gateway ------------------------


@requires_live_story
def test_the_investigation_event_stream_is_not_buffered_away(
    analyst: httpx.Client, completed_run: str
) -> None:
    """SSE must arrive as an unbuffered `text/event-stream`, through the proxy included.

    A rewrite placed behind the SPA catch-all, or an intermediary that buffers the response,
    turns the live investigation view into a blank panel — which no status-code check catches.
    """
    deadline = time.monotonic() + _STREAM_FRAME_DEADLINE_SECONDS
    with analyst.stream("GET", f"{_API}/investigations/{completed_run}/stream") as response:
        assert response.status_code == _OK
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                return
            if time.monotonic() > deadline:
                break
    pytest.fail("the event stream produced no data frame; an intermediary is buffering it")
