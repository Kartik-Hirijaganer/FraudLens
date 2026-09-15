"""Summary: Raw httpx SSE client for exact OpenAI-compatible benchmark measurements.

Key classes:
- OpenAiCompatibleStreamClient: retry-counting streamed chat client with TTFT and required usage.

Key functions:
- validate_base_url: allow HTTPS or explicitly configured private/loopback HTTP `/v1` endpoints.

Notes:
- The harness owns retries; the client never uses an SDK retry layer or estimates missing tokens.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from lib.study.urls import validate_origin_url
from lib.vllm_bench.config import RequestConfig
from lib.vllm_bench.state import BenchmarkCase, RequestMeasurement, TokenUsage

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_HTTP_ERROR_STATUS = 400


def validate_base_url(value: str, *, allowed_http_hosts: tuple[str, ...]) -> str:
    """Validate an OpenAI-compatible `/v1` base URL without credentials/query/fragment."""
    parsed = urlsplit(value)
    if parsed.path.rstrip("/") != "/v1" or parsed.query or parsed.fragment:
        raise ValueError("benchmark base URL must be an origin followed only by /v1")
    origin = validate_origin_url(
        f"{parsed.scheme}://{parsed.netloc}", allow_http_hosts=allowed_http_hosts
    )
    return origin + "/v1"


class OpenAiCompatibleStreamClient:
    """Measure raw OpenAI-compatible SSE streams with harness-owned bounded retries."""

    def __init__(  # noqa: PLR0913 - injected transport and clock make streaming deterministic.
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        request: RequestConfig,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Bind one endpoint and an injected transport/clock for deterministic tests."""
        if not api_key.strip():
            raise ValueError("VLLM_API_KEY is required")
        self._base_url = validate_base_url(
            base_url, allowed_http_hosts=request.allowed_plain_http_hosts
        )
        self._model = model
        self._request = request
        self._client = client or httpx.AsyncClient(timeout=request.timeout_s)
        self._owns_client = client is None
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._clock = clock

    async def close(self) -> None:
        """Close only the internally-created httpx client."""
        if self._owns_client:
            await self._client.aclose()

    def _payload(self, case: BenchmarkCase, seed: int) -> dict[str, object]:
        """Build the frozen request shared by both benchmark arms."""
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [message.model_dump(mode="json") for message in case.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self._request.temperature,
            "max_tokens": self._request.max_tokens,
        }
        if self._request.json_object_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._request.seed_requests:
            payload["seed"] = seed
        return payload

    async def generate(
        self, case: BenchmarkCase, *, sequence: int, seed: int
    ) -> RequestMeasurement:
        """Execute one request, counting bounded retries and returning a terminal observation."""
        started_at = datetime.now(UTC)
        start = self._clock()
        error_code = "request_failed"
        for attempt in range(1, self._request.max_attempts + 1):
            try:
                result = await self._attempt(case, sequence=sequence, seed=seed, attempt=attempt)
            except (httpx.TimeoutException, httpx.TransportError):
                error_code = "transport_error"
                if attempt < self._request.max_attempts:
                    continue
            else:
                retryable = result.error_code in {"retryable_http", "stream_error"}
                if retryable and attempt < self._request.max_attempts:
                    continue
                return result.model_copy(
                    update={"started_at": started_at, "latency_s": self._clock() - start}
                )
            break
        return RequestMeasurement(
            case_id=case.case_id,
            sequence=sequence,
            seed=seed,
            attempts=self._request.max_attempts,
            started_at=started_at,
            latency_s=self._clock() - start,
            error_code=error_code,
        )

    async def _attempt(
        self, case: BenchmarkCase, *, sequence: int, seed: int, attempt: int
    ) -> RequestMeasurement:
        """Consume one HTTP stream without persisting response headers or error bodies."""
        start = self._clock()
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers=self._headers,
            json=self._payload(case, seed),
        ) as response:
            if response.status_code >= _HTTP_ERROR_STATUS:
                return self._failure(
                    case,
                    sequence,
                    seed,
                    attempt,
                    start,
                    "retryable_http" if response.status_code in _RETRYABLE_STATUS else "http_error",
                )
            if "text/event-stream" not in response.headers.get("content-type", ""):
                return self._failure(case, sequence, seed, attempt, start, "invalid_content_type")
            content: list[str] = []
            ttft: float | None = None
            finish_reason: str | None = None
            usage: TokenUsage | None = None
            try:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if not isinstance(event, dict) or event.get("error") is not None:
                        raise ValueError("invalid stream event")
                    choices = event.get("choices") or []
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            if ttft is None:
                                ttft = self._clock() - start
                            content.append(str(text))
                        finish_reason = choice.get("finish_reason") or finish_reason
                    if event.get("usage") is not None:
                        usage = TokenUsage.model_validate(event["usage"])
            except (json.JSONDecodeError, TypeError, ValueError):
                return self._failure(case, sequence, seed, attempt, start, "stream_error")
        if usage is None:
            return self._failure(case, sequence, seed, attempt, start, "missing_usage")
        if ttft is None:
            return self._failure(case, sequence, seed, attempt, start, "empty_content")
        return RequestMeasurement(
            case_id=case.case_id,
            sequence=sequence,
            seed=seed,
            attempts=attempt,
            started_at=datetime.now(UTC),
            latency_s=self._clock() - start,
            ttft_s=ttft,
            content="".join(content),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _failure(  # noqa: PLR0913, PLR0917 - mirrors RequestMeasurement identity fields.
        self,
        case: BenchmarkCase,
        sequence: int,
        seed: int,
        attempt: int,
        start: float,
        code: str,
    ) -> RequestMeasurement:
        """Build a stable terminal failure without remote error text."""
        return RequestMeasurement(
            case_id=case.case_id,
            sequence=sequence,
            seed=seed,
            attempts=attempt,
            started_at=datetime.now(UTC),
            latency_s=self._clock() - start,
            error_code=code,
        )
