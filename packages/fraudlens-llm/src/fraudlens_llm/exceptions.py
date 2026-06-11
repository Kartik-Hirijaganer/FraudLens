"""Summary: Exception hierarchy for the standalone FraudLens LLM client. It keeps
catalog, provider, guardrail, policy, transport, and capability failures explicit
so callers can distinguish retryable provider failures from fail-closed security
decisions.

Key classes:
- LlmError: Base class for all library-raised exceptions.
- CatalogError: Raised when catalog/provider configuration cannot be loaded.
- ModelNotFoundError: Raised when a model reference is absent from the catalog.
- ProviderNotConfiguredError: Raised when a catalog provider has no connection config.
- MissingApiKeyError: Raised when a provider API key env var is unset.
- ProviderAuthError: Raised for provider authentication/authorization failures.
- ProviderError: Raised for provider-side failures that are not otherwise classified.
- LlmTimeoutError: Raised for provider timeout failures.
- LlmRateLimitError: Raised for provider rate-limit failures.
- GuardrailError: Raised when guardrails block a request or response.
- PolicyError: Raised when provider governance policy disallows a call.
- UnsupportedParameterError: Raised when a provider cannot honor a parameter.
- CapabilityMismatchError: Raised when a model/provider cannot perform the operation.

Key functions:
- (none)

Notes:
- Retry/fallback code relies on the retryable property rather than parsing messages.
"""

from __future__ import annotations


class LlmError(Exception):
    """Base class for all FraudLens LLM library exceptions."""

    retryable: bool = False


class CatalogError(LlmError):
    """Raised when catalog or provider config is invalid or unreadable."""


class ModelNotFoundError(CatalogError):
    """Raised when a provider/model reference does not exist in the catalog."""


class ProviderNotConfiguredError(CatalogError):
    """Raised when a catalog provider has no callable provider configuration."""


class MissingApiKeyError(LlmError):
    """Raised when the configured API key environment variable is missing."""


class ProviderAuthError(LlmError):
    """Raised for authentication or authorization failures returned by a provider."""


class ProviderError(LlmError):
    """Raised for provider-side errors that do not fit a more specific class."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        """Create a provider error with explicit retryability metadata."""
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LlmTimeoutError(ProviderError):
    """Raised when a provider request times out."""

    def __init__(self, message: str = "LLM provider request timed out") -> None:
        """Create a retryable timeout error."""
        super().__init__(message, retryable=True)


class LlmRateLimitError(ProviderError):
    """Raised when a provider returns a rate-limit response."""

    def __init__(self, message: str = "LLM provider rate limit exceeded") -> None:
        """Create a retryable rate-limit error."""
        super().__init__(message, retryable=True, status_code=429)


class GuardrailError(LlmError):
    """Raised when deterministic security guardrails block a call."""


class PolicyError(LlmError):
    """Raised when provider governance policy disallows a call."""


class UnsupportedParameterError(LlmError):
    """Raised when a requested generation parameter is not supported."""


class CapabilityMismatchError(LlmError):
    """Raised when a model or provider lacks the requested capability."""
