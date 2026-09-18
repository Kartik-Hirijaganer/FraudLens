"""Summary: Portfolio-demo preflight guards and active-model state matrix.

Key classes:
- BootstrapRefusedError: expose a PHI-free operator refusal.
- OperationalState: classify the configured tenant's persisted rows.
- ModelPromoter: define the injected model-promotion seam.
- BootstrapSummary: record aggregate bootstrap outcomes.

Key functions:
- acquire_story_lock: serialize story application in Postgres.
- assert_configured_tenant: enforce single-tenant demo ownership.
- assert_enabled_in_prod:
- assert_execution_modes:
- assert_rag_index: require a queryable RAG index before any story write.
- verify_model_bundle: validate the pinned model artifact.
- detect_operational_state:
- ensure_active_model: apply the guarded model-state matrix.

Notes:
- All guards execute before story writes.
"""

from __future__ import annotations

from collections.abc import Awaitable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import Agency, Transaction
from fraudlens_backend.db.repositories import AuditLogRepository, ModelRegistryRepository
from fraudlens_backend.db.repositories.model_registry import FIXTURE_MODEL_LABEL
from fraudlens_backend.portfolio_demo.config import AUDIT_ACTION, PortfolioDemoConfig
from fraudlens_backend.settings import AppSettings

_MANIFEST_SIDECAR = "manifest.json"
_METADATA_SIDECAR = "metadata.json"
_POSTGRES_DIALECT = "postgresql"
_RAG_INDEX_READY = "ready"


class BootstrapRefusedError(RuntimeError):
    """Raised when a guard refuses to proceed; the message is a PHI-free operator reason."""


class OperationalState(StrEnum):
    """What the configured tenant currently holds, judged by the derived external-id namespace."""

    EMPTY = "empty"
    STORY = "story"
    FOREIGN = "foreign"


class ModelPromoter(Protocol):
    """Registers + promotes the configured bundle to ACTIVE (`activate_model.py`'s chain).

    Injected rather than imported: the promotion chain lives in `scripts/`, which is not on the
    backend's import path, so the CLI supplies it and tests can supply the same function.
    """

    def __call__(
        self, session: AsyncSession, *, version_label: str
    ) -> Awaitable[str]:  # pragma: no cover - structural type
        """Promote `version_label` to ACTIVE and return a PHI-free outcome summary."""
        ...


class BootstrapSummary(BaseModel):
    """The PHI-free aggregate the story's `job_executions` row records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_version: str = Field(..., description="Story revision that was applied.")
    model_version_label: str = Field(..., description="Model label the story is pinned to.")
    model_outcome: str = Field(..., description="What the model-state matrix decided.")
    transactions_created: int = Field(..., ge=0, description="Authored rows newly inserted.")
    transactions_existing: int = Field(..., ge=0, description="Authored rows already present.")
    scored: int = Field(..., ge=0, description="Rows handed to the batch scorer this run.")
    already_scored: int = Field(..., ge=0, description="Rows skipped because a run already exists.")
    alert_transitions: int = Field(..., ge=0, description="Configured alert transitions applied.")
    sar_transitions: int = Field(..., ge=0, description="Configured SAR decisions applied.")
    verified: bool = Field(..., description="Whether verification matched the configured story.")


async def acquire_story_lock(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Take the story identity's Postgres advisory lock; refuse when another run holds it."""
    bind = session.get_bind()
    if bind.dialect.name != _POSTGRES_DIALECT:
        return
    held = (
        await session.execute(select(func.pg_try_advisory_lock(config.advisory_lock_key)))
    ).scalar_one()
    if not held:
        raise BootstrapRefusedError(
            "another portfolio demo bootstrap holds this story's advisory lock — retry once it ends"
        )


async def assert_configured_tenant(session: AsyncSession, config: PortfolioDemoConfig) -> None:
    """Confirm the DB agency IS the configured tenant and that no other agency persists."""
    agency = await session.get(Agency, config.agency.id)
    if agency is None:
        raise BootstrapRefusedError(
            "the configured portfolio demo agency does not exist — run the foundation seed first"
        )
    if agency.name != config.agency.name or agency.slug != config.agency.slug:
        raise BootstrapRefusedError(
            "the stored agency does not match the configured name/slug — reconcile the seed first"
        )
    others = (
        await session.execute(
            select(func.count()).select_from(Agency).where(Agency.id != agency.id)
        )
    ).scalar_one()
    if others:
        raise BootstrapRefusedError(
            f"{others} other agency row(s) exist; the portfolio demo owns exactly one tenant"
        )


def assert_enabled_in_prod(settings: AppSettings) -> None:
    """In prod the feature gate must be explicitly on; the Python default fails closed."""
    if settings.environment == "prod" and not settings.portfolio_demo_enabled:
        raise BootstrapRefusedError(
            "portfolio demo mode is disabled — set portfolio_demo_enabled to bootstrap in prod"
        )


def assert_execution_modes(config: PortfolioDemoConfig, settings: AppSettings) -> None:
    """Confirm the runtime provider modes are the deterministic ones the story assumes.

    `execution.llm_mode` / `execution.rag_embedding_mode` exist so the pinned distribution names the
    providers it was calibrated against; running the bootstrap under different ones would produce
    SAR narratives and retrievals the story never verified.
    """
    mismatches = [
        f"{field} is '{actual}' but the story assumes '{expected}'"
        for field, actual, expected in (
            ("llm_mode", settings.llm_mode, config.execution.llm_mode),
            (
                "rag_embedding_mode",
                settings.rag_embedding_mode,
                config.execution.rag_embedding_mode,
            ),
        )
        if actual != expected
    ]
    if mismatches:
        raise BootstrapRefusedError("; ".join(mismatches))


def assert_rag_index(settings: AppSettings) -> None:
    """Confirm the retriever can serve the citations every story SAR has to carry.

    Retrieval is a soft enhancer everywhere else — it degrades to empty and the investigation
    continues — but the quality-gated SAR cascade (ADR-030) rejects a draft citing no regulation,
    and a case retrieval offered NOTHING for is TERMINAL: no later tier can cite what was never
    retrieved. The story pins five drafted SARs and no failed one, so an index the retriever cannot
    query turns every one of them into `failed`, which stamps `sar_unavailable` on its alert and
    raises it `pending_review` instead of the `open` the story declares.

    That makes a usable index a precondition of the pinned distribution exactly as the provider
    modes above are, so it is asserted HERE, at zero writes — before `--reset` deletes a live story
    the rebuild could not then reproduce. The index is resolved through the same anchor, collection
    and embedder `build_pipeline_components` builds the retriever from, so this checks the index
    that will actually be queried rather than a second guess at where it lives.
    """
    # Lazy, like `verify_model_bundle` below: keeps heavy chromadb out of the import graph.
    from fraudlens_backend.pipeline_wiring import _anchored  # noqa: PLC0415 - heavy import
    from fraudlens_backend.rag import build_embedder  # noqa: PLC0415 - heavy import
    from fraudlens_ml.rag import index_status  # noqa: PLC0415 - heavy import

    index_dir = _anchored(settings.rag_index_dir)
    status = index_status(index_dir, settings.rag_collection, build_embedder(settings).provenance)
    if status != _RAG_INDEX_READY:
        raise BootstrapRefusedError(
            f"the RAG index is '{status}' for the configured embedding space, so retrieval would "
            "offer no citation and the SAR quality gate would fail every draft the story pins — "
            "build it with `make ingest-rag` before bootstrapping"
        )


def verify_model_bundle(config: PortfolioDemoConfig, models_dir: Path) -> None:
    """Verify the pinned bundle's presence, sidecars, feature spec, and booster checksum."""
    from fraudlens_ml.scoring import ArtifactError, load_artifact  # noqa: PLC0415 - heavy import

    bundle = models_dir / config.model.version_label
    for sidecar in (_METADATA_SIDECAR, _MANIFEST_SIDECAR):
        if not (bundle / sidecar).is_file():
            raise BootstrapRefusedError(
                f"the pinned model bundle is missing its '{sidecar}' sidecar — train or fetch it"
            )
    try:
        loaded = load_artifact(bundle)  # loading re-verifies the booster checksum
    except ArtifactError as exc:
        raise BootstrapRefusedError(f"the pinned model bundle is unusable: {exc}") from exc
    if loaded.version_label != config.model.version_label:
        raise BootstrapRefusedError(
            "the pinned bundle's version label does not match the configuration"
        )
    if loaded.feature_spec.version != config.model.feature_spec_version:
        raise BootstrapRefusedError(
            "the pinned bundle's feature-spec version does not match the configuration"
        )


async def detect_operational_state(
    session: AsyncSession, config: PortfolioDemoConfig
) -> OperationalState:
    """Classify the tenant: empty, only configured story rows, or holding foreign rows."""
    stored = set(
        (
            await session.execute(
                select(Transaction.external_id).where(Transaction.agency_id == config.agency.id)
            )
        )
        .scalars()
        .all()
    )
    if not stored:
        return OperationalState.EMPTY
    configured = {config.external_id(scenario) for scenario in config.scenarios}
    return OperationalState.STORY if stored <= configured else OperationalState.FOREIGN


# --------------------------------------------------------------------------------------------------
# Model-state matrix
# --------------------------------------------------------------------------------------------------


async def ensure_active_model(
    session: AsyncSession,
    config: PortfolioDemoConfig,
    *,
    promote: ModelPromoter,
    audit: AuditLogRepository,
) -> str:
    """Resolve the four-way model-state matrix and return a PHI-free outcome summary.

    Configured model already active ⇒ verify only. No active model ⇒ register + promote. The
    seeded fixture active ⇒ promote the configured model and audit the flip. Any OTHER non-fixture
    model active ⇒ refuse: silently displacing an operator's promotion is not the bootstrap's call.
    """
    label = config.model.version_label
    pointer = await ModelRegistryRepository(session).build_pointer()
    if pointer is not None and pointer.active_version_label == label:
        return "configured model already active"
    if pointer is not None and pointer.active_version_label != FIXTURE_MODEL_LABEL:
        raise BootstrapRefusedError(
            f"a different model is active ('{pointer.active_version_label}'); the portfolio demo "
            f"will not displace it — activate '{label}' deliberately instead"
        )
    outcome = await promote(session, version_label=label)
    await audit.record(
        actor_id=None,
        action=AUDIT_ACTION,
        resource_type="model_version",
        resource_id=label,
        metadata={
            "step": "activate_model",
            "from": FIXTURE_MODEL_LABEL if pointer is not None else "none",
            "to": label,
        },
    )
    return outcome
