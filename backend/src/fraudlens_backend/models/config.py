"""Summary: Pydantic models for the admin runtime-configuration API. The wire shape is
camelCase while Python internals stay snake_case, and config values are intentionally generic JSON
because `system_config` stores heterogeneous tunables such as thresholds, label-maturity windows,
and model-gate objects. Responses include keys, scope, value, and update timestamps; audit rows
record only keys/scope, never values, so secrets or PHI cannot leak through audit metadata.

Key classes:
- ConfigEntry: one global or tenant-scoped runtime config key/value row.
- ConfigListResponse: GET /config response containing visible global + tenant rows.
- ConfigPatchRequest: PATCH /config request for creating/updating one key.
- ConfigPatchResponse: PATCH /config response wrapping the updated entry.

Key functions:
- (none)

Notes:
- This API is admin-only and is for runtime tunables, not boot-critical or secret config. Secrets
  remain Infisical-managed and never belong in `system_config`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from fraudlens_backend.models.common import CamelModel


class ConfigEntry(CamelModel):
    """One runtime config row; agencyId is null for global defaults."""

    config_id: str = Field(..., description="The system_config row id.")
    key: str = Field(..., description="Stable runtime config key.")
    value: Any = Field(..., description="JSON config value for the key.")
    agency_id: str | None = Field(
        default=None, description="Owning agency id, or null for a global default."
    )
    updated_at: datetime = Field(..., description="When the config row was last updated.")


class ConfigListResponse(CamelModel):
    """Runtime config rows visible to the tenant admin."""

    config: list[ConfigEntry] = Field(
        default_factory=list, description="Global rows plus rows scoped to the caller's agency."
    )


class ConfigPatchRequest(CamelModel):
    """Create/update one runtime config key."""

    key: str = Field(..., min_length=1, max_length=128, description="Runtime config key.")
    value: Any = Field(..., description="JSON config value to store.")
    agency_scoped: bool = Field(
        default=False,
        description="When true, store the override for the caller's agency; otherwise global.",
    )


class ConfigPatchResponse(CamelModel):
    """The updated runtime config row."""

    entry: ConfigEntry = Field(..., description="Updated runtime config row.")
