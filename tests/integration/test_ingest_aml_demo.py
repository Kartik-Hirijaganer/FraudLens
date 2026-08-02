"""Real IBM AML demo-ingest tests: deterministic case-pack selection (laundering
neighborhoods + benign controls), canonical masking, idempotency, tenant-scoped persistence,
and label hygiene. Fixtures contain only synthetic values."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from portfolio_demo_identity import DEMO_AGENCY_ID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fetch_dataset import DatasetFile, DatasetPaths
from fraudlens_backend.db.models import Agency, Transaction
from fraudlens_backend.portfolio_demo import load_portfolio_demo_config
from ingest_aml_demo import ingest_demo_transactions
from lib.aml_fraud import IBM_AML, load_ibm_case_pack
from lib.gfp.partitions import RESEARCH_PARTITIONS


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
    config = load_portfolio_demo_config()
    transactions = load_ibm_case_pack(
        _paths(tmp_path),
        rows=6,
        agency_count=config.case_pack_partition_count,
        tenant_weights=config.case_pack_tenant_weights,
    )
    assert len(transactions) == 6
    first = await ingest_demo_transactions(db_session, transactions, config)
    second = await ingest_demo_transactions(db_session, transactions, config)

    assert first.accepted == 6
    assert second.duplicates == 6
    # Exactly ONE persistent runtime tenant exists; research partitions are an offline concept.
    assert (await db_session.execute(select(func.count()).select_from(Agency))).scalar_one() == 1

    rows = (await db_session.execute(select(Transaction))).scalars().all()
    assert len(rows) == 6
    assert all("\x1f" not in row.origin_account for row in rows)
    # The public label is never persisted — the canonical features carry only the source tag.
    assert all(row.features == {"dataset_source": IBM_AML} for row in rows)


async def test_case_pack_keeps_the_laundering_neighborhood_in_one_tenant(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    config = load_portfolio_demo_config()
    transactions = load_ibm_case_pack(
        _paths(tmp_path),
        rows=6,
        agency_count=config.case_pack_partition_count,
        tenant_weights=config.case_pack_tenant_weights,
    )
    await ingest_demo_transactions(db_session, transactions, config)

    # The anchor's whole neighborhood (3 rows touching account LNDR) lives in ONE tenant —
    # the configured demo agency — so its served history windows match training windows.
    primary = DEMO_AGENCY_ID
    neighborhood = [item for item in transactions if item.agency_index == 0]
    assert len(neighborhood) >= 3
    count = (
        await db_session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.agency_id == primary)
        )
    ).scalar_one()
    assert count == len(neighborhood)


def test_case_pack_still_spreads_across_partitions_when_asked(tmp_path: Path) -> None:
    """Multi-partition selection is intact — the demo's single tenant is a CONFIG choice.

    The offline GFP study keeps partitioning IBM rows across `RESEARCH_PARTITIONS`, so the
    loader must still honour a wider partition count and anchor-weight cycle.
    """
    transactions = load_ibm_case_pack(
        _paths(tmp_path),
        rows=6,
        agency_count=len(RESEARCH_PARTITIONS),
        tenant_weights=(0, 0, 0, 1, 2),
    )
    assert len({item.agency_index for item in transactions}) >= 2


def test_the_configured_weights_change_the_split_but_the_study_default_does_not(
    tmp_path: Path,
) -> None:
    """`case_pack_tenant_weights` steers the split; omitting it leaves the GFP study path alone.

    Phase 2 parameterised `load_ibm_case_pack` so the single-tenant portfolio demo could ask for
    one partition. That freedom is only safe if the DEFAULT call — the one `benchmark_gfp.py`
    makes — still produces the study's own spread, because the committed artifact indexes its
    cross-tenant motifs by partition position.
    """
    paths = _paths(tmp_path)
    partitions = len(RESEARCH_PARTITIONS)

    study_default = load_ibm_case_pack(paths, rows=6, agency_count=partitions)
    explicit_default = load_ibm_case_pack(
        paths, rows=6, agency_count=partitions, tenant_weights=(0, 0, 0, 1, 2)
    )
    single_tenant = load_ibm_case_pack(
        paths,
        rows=6,
        agency_count=load_portfolio_demo_config().case_pack_partition_count,
        tenant_weights=load_portfolio_demo_config().case_pack_tenant_weights,
    )

    # Omitting the argument is byte-for-byte the study's historical behaviour.
    assert [item.agency_index for item in study_default] == [
        item.agency_index for item in explicit_default
    ]
    # ...while the configured single-partition cycle collapses every row into one partition.
    assert {item.agency_index for item in single_tenant} == {0}
    assert len({item.agency_index for item in study_default}) > 1
