"""Summary: The frontend client-error sink (plan §5.3 endpoint 27, §11). The SPA's global
error/unhandledrejection handlers POST scrubbed client errors here, through the gateway, so
retention and PHI policy stay centralized server-side rather than going to a third-party
analytics service. The endpoint requires a valid JWT, is rate-limited at the gateway like
every route AND carries a stricter per-route limiter (api/deps.rate_limit) as defense-in-depth
for this abuse-prone client-driven sink (plan §16 Phase 13), masks the message + any context
values with the deterministic PHI masker BEFORE they are logged, truncates the message to a
configured cap, and returns 202 with no body. Nothing is persisted — the scrubbed report is
emitted to the structured log (where the redaction processor is a second, independent net)
and correlated by the gateway request-id.

Key classes:
- (none)

Key functions:
- report_client_error: accept, scrub, and log a frontend error report (202).

Notes:
- Masking happens here AND again in the logging redaction processor (defense-in-depth), so
  a PHI-shaped value cannot reach the log even if one layer is bypassed.
- The tenant agency_id is bound for correlation; it is a tenant id, not PHI, and is not on
  the logging key denylist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import Response

from fraudlens_backend.api.deps import SettingsDep, get_tenant, rate_limit
from fraudlens_backend.middleware.logging import APP_LOGGER_NAME, get_logger
from fraudlens_backend.models.common import TenantContext
from fraudlens_backend.models.transactions import ClientErrorReport
from fraudlens_core.phi import mask_text

router = APIRouter(tags=["telemetry"])

TenantDep = Annotated[TenantContext, Depends(get_tenant)]

# A stricter per-route limiter (defense-in-depth beyond the global gateway limit) for this
# abuse-prone, client-driven sink; the budget is config-driven (plan §16 Phase 13).
_client_error_rate_limit = rate_limit(
    "client_error",
    limit=lambda settings: settings.client_error_rate_limit_requests,
    window=lambda settings: settings.rate_limit_window_seconds,
)


@router.post(
    "/telemetry/client-error",
    status_code=202,
    dependencies=[Depends(_client_error_rate_limit)],
)
async def report_client_error(
    payload: ClientErrorReport, tenant: TenantDep, settings: SettingsDep
) -> Response:
    """Scrub + log a frontend client-error report (PHI-masked, truncated); return 202."""
    message = mask_text(payload.message[: settings.client_error_max_message_length]).value
    context = {key: mask_text(str(value)).value for key, value in (payload.context or {}).items()}
    get_logger(APP_LOGGER_NAME).info(
        "client_error", agency_id=tenant.agency_id, client_message=message, context=context
    )
    return Response(status_code=202)
