"""Summary: Canonical rule findings on positive cases exchanged by ranking policies.

Key classes:
- (none)

Key functions:
- positive_patterns: rebuild strict bounded histories and verify cached rule scores.
- summarize_patterns: aggregate named patterns on gained/lost positive cases.

Notes:
- Synthetic account identities remain transient and are never returned in reports.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa

from fraudlens_core import (
    DEFAULT_RULE_DEFINITIONS,
    RuleContext,
    RuleRegistry,
    RuleTransaction,
    TransactionDirection,
)
from lib.aml_mapping import ibm_channel, ibm_country, ibm_currency
from lib.fulldata.config import load_fulldata_config
from lib.risk_study.models import Cohort, StudyRun


def _transaction(row: tuple[Any, ...], *, inbound: bool) -> RuleTransaction:
    epoch, amount, currency, payment = row
    return RuleTransaction(
        amount=amount,
        currency=ibm_currency(currency),
        country=ibm_country(currency),
        channel=ibm_channel(payment),
        occurred_at=datetime.fromtimestamp(epoch, UTC),
        direction=TransactionDirection.INBOUND if inbound else TransactionDirection.OUTBOUND,
    )


def positive_patterns(root: Path, run: StudyRun, cohort: Cohort) -> dict[int, tuple[str, ...]]:
    """Independently replay all holdout positives through RuleRegistry, without raw export."""
    indices = np.flatnonzero(cohort.holdout & (cohort.label == 1))
    if not len(indices):
        return {}
    source = load_fulldata_config().dataset(run.candidate)
    scan = str(root / run.work_dir / "input/ingested" / source.source / "month=*/part-*.parquet")
    window_hours = max(int(d.params.get("windowHours", 0)) for d in DEFAULT_RULE_DEFINITIONS)
    history_max = load_fulldata_config().features.history_max
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='4GB'")
        ids = pa.table({"row_id": cohort.row_id[indices]})
        connection.register("positive_ids", ids)
        connection.execute(
            "CREATE TEMP TABLE positives AS SELECT s.* FROM read_parquet(?, "
            "hive_partitioning=false) s JOIN positive_ids USING(row_id)",
            [scan],
        )
        connection.execute(
            "CREATE TEMP TABLE accounts AS SELECT DISTINCT origin_account AS account FROM positives"
        )
        targets = connection.execute(
            "SELECT row_id,origin_account,cohort_key,amount,payment_currency,"
            "payment_format FROM positives ORDER BY cohort_key,row_id"
        ).fetchall()
        events = connection.execute(
            """
            WITH base AS (
              SELECT * FROM read_parquet(?, hive_partitioning=false)
              WHERE origin_account IN (SELECT account FROM accounts)
                 OR dest_account IN (SELECT account FROM accounts)
            ), events AS (
              SELECT origin_account AS account,cohort_key,row_id,amount,payment_currency,
                payment_format,false AS inbound FROM base
              UNION ALL
              SELECT dest_account,cohort_key,row_id,amount,payment_currency,payment_format,true
                FROM base WHERE origin_account <> dest_account
            ) SELECT e.* FROM events e JOIN accounts a USING(account)
              ORDER BY account,cohort_key,row_id
        """,
            [scan],
        ).fetchall()
    histories: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for account, epoch, row_id, amount, currency, payment, inbound in events:
        histories[account].append(
            (int(epoch), int(row_id), amount, currency, payment, bool(inbound))
        )
    times = {account: [event[0] for event in history] for account, history in histories.items()}
    registry = RuleRegistry()
    cached = {int(cohort.row_id[i]): float(cohort.rules[i]) for i in indices}
    result: dict[int, tuple[str, ...]] = {}
    for row_id, account, epoch, amount, currency, payment in targets:
        moments = times[account]
        end = bisect_left(moments, epoch)
        start = max(bisect_left(moments, epoch - window_hours * 3600), end - history_max)
        priors = histories[account][start:end]
        context = RuleContext(
            transaction=_transaction((epoch, amount, currency, payment), inbound=False),
            history=tuple(_transaction((p[0], p[2], p[3], p[4]), inbound=p[5]) for p in priors),
        )
        evaluation = registry.evaluate(DEFAULT_RULE_DEFINITIONS, context)
        if evaluation.errored_rules or float(evaluation.subscore) != cached[int(row_id)]:
            raise ValueError("canonical positive-case rules disagree with cached study input")
        result[int(row_id)] = tuple(hit.code for hit in evaluation.hits)
    if len(result) != len(indices):
        raise ValueError("positive case reconstruction did not reconcile")
    return result


def summarize_patterns(row_ids: np.ndarray, patterns: dict[int, tuple[str, ...]]) -> dict[str, int]:
    """Count overlapping explicit findings; these are pattern flags, not fraud diagnoses."""
    counter: Counter[str] = Counter()
    for row_id in row_ids:
        codes = patterns[int(row_id)]
        counter.update(codes or ("no_rule_hit",))
    return dict(sorted(counter.items()))
