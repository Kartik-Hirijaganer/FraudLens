"""Real IBM AML demo-ingest tests: deterministic case-pack selection (laundering
neighborhoods + benign controls), canonical masking, idempotency, tenant-scoped persistence,
and label hygiene. Fixtures contain only synthetic values."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fetch_dataset import DatasetFile, DatasetPaths
from fraudlens_backend.db.models import Agency, Transaction
from fraudlens_backend.demo import AML_DEMO_AGENCIES
from ingest_aml_demo import ingest_demo_transactions
from lib.aml_fraud import IBM_AML, load_ibm_case_pack


def _paths(tmp_path: Path) -> DatasetPaths:
    """Write a synthetic IBM-shaped CSV: one laundering neighborhood + spread benign controls.

    Bank digits hash deterministically across the three demo tenants ("0"->0, "1"->1, "2"->2),
    so the benign controls cover every partition while the laundering anchor (bank 7, account
    LNDR) claims its whole neighborhood for the primary tenant.
    """
    rows = [
        # The laundering anchor's neighborhood: its laundering row + same-account context
        # (as sender and as receiver) inside the ±3-day window.
        {
            "Timestamp": "2022-01-05 10:00:00",
            "From Bank": "7",
            "Account": "LNDR",
            "To Bank": "0",
            "Account.1": "2000",
            "Amount Paid": "9100.00",
            "Payment Currency": "US Dollar",
            "Payment Format": "Wire",
            "Is Laundering": "1",
        },
        {
            "Timestamp": "2022-01-04 09:00:00",
            "From Bank": "7",
            "Account": "LNDR",
            "To Bank": "1",
            "Account.1": "2001",
            "Amount Paid": "500.00",
            "Payment Currency": "US Dollar",
            "Payment Format": "ACH",
            "Is Laundering": "0",
        },
        {
            "Timestamp": "2022-01-06 12:00:00",
            "From Bank": "2",
            "Account": "2002",
            "To Bank": "7",
            "Account.1": "LNDR",
            "Amount Paid": "750.00",
            "Payment Currency": "Euro",
            "Payment Format": "Cash",
            "Is Laundering": "0",
        },
        # Benign controls spread across the three deterministic bank partitions.
        *[
            {
                "Timestamp": f"2022-02-0{index + 1} 00:00:00",
                "From Bank": str(index),
                "Account": f"100{index}",
                "To Bank": str(index + 10),
                "Account.1": f"300{index}",
                "Amount Paid": f"{9000 + index}.00",
                "Payment Currency": "US Dollar",
                "Payment Format": "Wire",
                "Is Laundering": "0",
            }
            for index in range(3)
        ],
    ]
    frame = pd.DataFrame(rows)
    target = tmp_path / "HI-Small_Trans.csv"
    frame.to_csv(target, index=False)
    return DatasetPaths(
        source=IBM_AML,
        directory=str(tmp_path),
        files=[DatasetFile(name=target.name, sha256="0" * 64, row_count=len(frame))],
    )


async def test_ingest_case_pack_partitions_masks_and_deduplicates(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    transactions = load_ibm_case_pack(_paths(tmp_path), rows=6, agency_count=len(AML_DEMO_AGENCIES))
    assert len(transactions) == 6
    first = await ingest_demo_transactions(db_session, transactions)
    second = await ingest_demo_transactions(db_session, transactions)

    assert first.accepted == 6
    assert second.duplicates == 6
    assert (await db_session.execute(select(func.count()).select_from(Agency))).scalar_one() == 3

    rows = (await db_session.execute(select(Transaction))).scalars().all()
    assert len(rows) == 6
    assert all("\x1f" not in row.origin_account for row in rows)
    # The public label is never persisted — the canonical features carry only the source tag.
    assert all(row.features == {"dataset_source": IBM_AML} for row in rows)


async def test_case_pack_keeps_the_laundering_neighborhood_in_one_tenant(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    transactions = load_ibm_case_pack(_paths(tmp_path), rows=6, agency_count=len(AML_DEMO_AGENCIES))
    await ingest_demo_transactions(db_session, transactions)

    # The anchor's whole neighborhood (3 rows touching account LNDR) lives in ONE tenant —
    # the primary demo agency — so its served history windows match training windows.
    primary = AML_DEMO_AGENCIES[0].agency_id
    neighborhood = [item for item in transactions if item.agency_index == 0]
    assert len(neighborhood) >= 3
    count = (
        await db_session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.agency_id == primary)
        )
    ).scalar_one()
    assert count == len(neighborhood)
    # Benign controls still exercise more than one tenant partition.
    assert len({item.agency_index for item in transactions}) >= 2
