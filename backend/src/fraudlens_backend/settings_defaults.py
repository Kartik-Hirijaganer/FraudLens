"""Summary: Shared type aliases and safe defaults for backend settings.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- Values here are non-secret process defaults; deployment-specific values remain in config YAML.
"""

from typing import Literal

Environment = Literal["dev", "prod", "staging"]
StorageBackend = Literal["local", "azure_blob"]
QueueBackend = Literal["local", "container_apps_jobs"]
LlmMode = Literal["mock", "live"]
RagEmbeddingMode = Literal["offline", "live"]
SecretsDelivery = Literal["unconfigured", "externally_injected"]

DEFAULT_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)
