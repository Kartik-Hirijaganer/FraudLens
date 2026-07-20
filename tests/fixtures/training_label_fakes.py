"""Summary: Test-only builders for reviewed, matured training-label evidence.

Key classes:
- (none)

Key functions:
- add_matured_training_labels: create balanced labels backed by transactions and completed runs.

Notes:
- These fixtures are intentionally synthetic and exist only inside the behavioral test suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from fraudlens_backend.db.models import (
    AnalysisRun,
    LabelSource,
    RunStatus,
    TrainingLabel,
    TrainingLabelType,
    Transaction,
)

_DEMO_AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
_DEMO_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
_LABEL_CYCLE = (
    TrainingLabelType.CONFIRMED_FRAUD,
    TrainingLabelType.BENIGN,
    TrainingLabelType.FALSE_NEGATIVE,
    TrainingLabelType.FALSE_POSITIVE,
)


async def add_matured_training_labels(session: AsyncSession, *, count: int = 12) -> list[uuid.UUID]:
    """Create balanced, already-matured label evidence and return its analysis-run ids."""
    matured_at = datetime.now(UTC) - timedelta(days=1)
    run_ids: list[uuid.UUID] = []
    fixture_scope = uuid.uuid4().hex
    for index in range(count):
        transaction = Transaction(
            agency_id=_DEMO_AGENCY_ID,
            external_id=f"label-fixture-{fixture_scope}-{index}",
            amount=Decimal("100.00") + index,
            currency="USD",
            occurred_at=matured_at,
            origin_account="********1111",
            dest_account="********2222",
            channel="wire",
            country="US",
            features={"dataset_source": "test-fixture"},
            feature_hash=f"{index:064x}",
        )
        session.add(transaction)
        await session.flush()
        run = AnalysisRun(
            agency_id=_DEMO_AGENCY_ID,
            transaction_id=transaction.id,
            status=RunStatus.COMPLETED,
            model_version="test-fixture",
        )
        session.add(run)
        await session.flush()
        session.add(
            TrainingLabel(
                agency_id=_DEMO_AGENCY_ID,
                transaction_id=transaction.id,
                run_id=run.id,
                label=_LABEL_CYCLE[index % len(_LABEL_CYCLE)],
                source=LabelSource.ANALYST_REVIEW,
                matured_at=matured_at,
                created_by=_DEMO_USER_ID,
            )
        )
        run_ids.append(run.id)
    await session.flush()
    return run_ids
