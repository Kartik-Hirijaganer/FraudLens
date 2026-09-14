"""Summary: Raw SSE, TTFT, token-usage, retry, and URL-policy client tests.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- httpx MockTransport provides the endpoint; no sockets are opened.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai_compatible_fake import CapturedOpenAiEndpoint
from vllm_bench_fakes import benchmark_case, sar_response

from lib.vllm_bench.client import OpenAiCompatibleStreamClient, validate_base_url
from lib.vllm_bench.config import load_config


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


async def _generate(handler) -> tuple[object, httpx.AsyncClient]:
    config = load_config()
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAiCompatibleStreamClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        model=config.arms["bf16"].model,
        request=config.request,
        client=transport,
        clock=_Clock(),
    )
    return await client.generate(benchmark_case(), sequence=0, seed=42), transport


@pytest.mark.asyncio
async def test_sse_records_first_delta_usage_and_exact_request_controls() -> None:
    endpoint = CapturedOpenAiEndpoint(sar_response())
    result, transport = await _generate(endpoint)
    await transport.aclose()
    assert result.error_code is None
    assert result.ttft_s == pytest.approx(0.1)
    assert result.usage.total_tokens == 20
    body = json.loads(endpoint.request_bodies[0])
    assert body["stream_options"] == {"include_usage": True}
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0
    assert body["seed"] == 42


@pytest.mark.asyncio
async def test_retry_is_harness_owned_and_counted() -> None:
    endpoint = CapturedOpenAiEndpoint(sar_response(), failures=1)
    result, transport = await _generate(endpoint)
    await transport.aclose()
    assert result.attempts == 2
    assert len(endpoint.request_bodies) == 2
    assert endpoint.request_bodies[0] == endpoint.request_bodies[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "code"),
    (
        (
            lambda request: httpx.Response(
                200, request=request, headers={"content-type": "application/json"}, json={}
            ),
            "invalid_content_type",
        ),
        (
            lambda request: httpx.Response(
                404,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=b"",
            ),
            "http_error",
        ),
        (
            lambda request: httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=b"data: {bad}\n\n",
            ),
            "stream_error",
        ),
        (
            lambda request: httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
            ),
            "missing_usage",
        ),
        (
            lambda request: httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices":[],"usage":{"prompt_tokens":1,'
                    b'"completion_tokens":0,"total_tokens":1}}\n\n'
                ),
            ),
            "empty_content",
        ),
    ),
)
async def test_stream_failures_are_stable_codes(handler, code: str) -> None:
    result, transport = await _generate(handler)
    await transport.aclose()
    assert result.error_code == code


@pytest.mark.asyncio
async def test_transport_failure_exhausts_bounded_attempts() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)

    result, transport = await _generate(timeout)
    await transport.aclose()
    assert result.error_code == "transport_error"
    assert result.attempts == load_config().request.max_attempts


def test_base_url_and_api_key_policy() -> None:
    config = load_config()
    assert validate_base_url(
        "http://localhost:8000/v1/", allowed_http_hosts=("localhost",)
    ).endswith("/v1")
    for value in (
        "http://example.com/v1",
        "https://example.com/not-v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?secret=x",
    ):
        with pytest.raises(ValueError):
            validate_base_url(value, allowed_http_hosts=("localhost",))
    with pytest.raises(ValueError, match="required"):
        OpenAiCompatibleStreamClient(
            base_url=config.server.base_url,
            api_key=" ",
            model=config.arms["bf16"].model,
            request=config.request,
        )
