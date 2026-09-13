"""Summary: In-memory OpenAI-compatible HTTP endpoint that captures exact request bytes.

Key classes:
- CapturedOpenAiEndpoint: deterministic retry/fallback-capable httpx transport handler.

Key functions:
- (none)

Notes:
- The fixture performs no socket IO and returns standards-shaped streaming chat chunks.
"""

from __future__ import annotations

import json

import httpx


class CapturedOpenAiEndpoint:
    """Capture serialized requests and fail a configured number before returning a SAR stream."""

    def __init__(self, response_text: str, *, failures: int = 0) -> None:
        """Bind the deterministic response and number of initial HTTP 500 responses."""
        self.response_text = response_text
        self.failures = failures
        self.request_bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record one raw body and return either a retryable error or an SSE completion."""
        self.request_bodies.append(request.content)
        if self.failures:
            self.failures -= 1
            return httpx.Response(
                500,
                request=request,
                json={"error": {"message": "synthetic failure", "type": "server_error"}},
            )
        events = (
            {
                "id": "quality-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "chat",
                "choices": [
                    {"index": 0, "delta": {"content": self.response_text}, "finish_reason": None}
                ],
            },
            {
                "id": "quality-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "chat",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body.encode("utf-8"),
        )
