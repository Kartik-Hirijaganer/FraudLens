"""Summary: Shared provider SDK error mapping for private adapters. It converts
provider-specific SDK exception classes into the FraudLens LLM exception hierarchy
with explicit retryability metadata.

Key classes:
- (none)

Key functions:
- map_provider_error: Map SDK exceptions to library exceptions.

Notes:
- Adapters pass their SDK-specific exception classes into this helper.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from fraudlens_llm.exceptions import (
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    ProviderAuthError,
    ProviderError,
)


def map_provider_error(  # noqa: PLR0913 - explicit SDK exception class map.
    exc: BaseException,
    provider: str,
    *,
    timeout_error: type[BaseException],
    rate_limit_error: type[BaseException],
    auth_error: type[BaseException],
    connection_error: type[BaseException],
    bad_request_error: type[BaseException],
    status_error: type[BaseException],
    retryable_status_codes: Iterable[int],
) -> LlmError:
    """Map provider SDK exceptions into library exceptions."""
    retryable_codes = set(retryable_status_codes)
    mapped: LlmError
    if isinstance(exc, timeout_error):
        mapped = LlmTimeoutError(f"Provider '{provider}' request timed out")
    elif isinstance(exc, rate_limit_error):
        mapped = LlmRateLimitError(f"Provider '{provider}' rate limit exceeded")
    elif isinstance(exc, auth_error):
        mapped = ProviderAuthError(f"Provider '{provider}' authentication failed")
    elif isinstance(exc, connection_error):
        mapped = ProviderError(f"Provider '{provider}' connection failed", retryable=True)
    elif isinstance(exc, bad_request_error):
        mapped = ProviderError(f"Provider '{provider}' rejected the request", retryable=False)
    elif isinstance(exc, status_error):
        status_code = cast(int | None, getattr(exc, "status_code", None))
        retryable = status_code in retryable_codes
        mapped = ProviderError(
            f"Provider '{provider}' returned a transient error"
            if retryable
            else f"Provider '{provider}' returned an error",
            retryable=retryable,
            status_code=status_code,
        )
    else:
        mapped = LlmError(f"Provider '{provider}' SDK error")
    return mapped
