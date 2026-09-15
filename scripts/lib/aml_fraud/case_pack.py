"""Summary: Deterministic sampling, row mapping, and IBM demo case-pack construction.

Key classes:
- (none)

Key functions:
- source_columns: expose the pinned source schema.
- sample_frame: seeded label-stratified sampling.
- demo_agency_index: deterministic synthetic tenant partitioning.
- map_ibm_demo_row: map one source row through canonical ingest validation.
- load_ibm_case_pack: build laundering neighborhoods plus benign controls.

Notes:
- Public labels guide offline selection only and are never persisted as application facts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from fraudlens_core import SchemaValidationError, build_canonical
from lib.aml_mapping import (
    ibm_account_key,
    ibm_channel,
    ibm_country,
    ibm_currency,
)

if TYPE_CHECKING:
    from fetch_dataset import DatasetPaths

from lib.aml_fraud.frames import (
    _ANCHOR_MIN,
    _ANCHOR_ROW_DIVISOR,
    _BENIGN_LABEL,
    _BENIGN_OVERSAMPLE,
    _CASE_PACK_CHUNK_ROWS,
    _COL_AMOUNT_PAID,
    _COL_FROM_ACCOUNT,
    _COL_FROM_BANK,
    _COL_IEEE_LABEL,
    _COL_IS_LAUNDERING,
    _COL_PAYMENT_CURRENCY,
    _COL_PAYMENT_FORMAT,
    _COL_TIMESTAMP,
    _COL_TO_ACCOUNT,
    _COL_TO_BANK,
    _DEFAULT_DEMO_AGENCIES,
    _DEFAULT_TENANT_WEIGHTS,
    _DEMO_EXTERNAL_ID_DIGEST_LENGTH,
    _DEMO_EXTERNAL_ID_PREFIX,
    _IBM_KEEP_COLUMNS,
    _IEEE_KEEP_COLUMNS,
    _LAUNDERING_LABEL,
    _NEIGHBORHOOD_MAX_ROWS,
    _NEIGHBORHOOD_WINDOW_SECONDS,
    IBM_AML,
    IEEE_CIS,
    IbmDemoTransaction,
    _unsupported,
)


def source_columns(source: str) -> tuple[str, ...]:
    """Return the raw dataset columns consumed for `source` (the manifest's schema record)."""
    if source == IBM_AML:
        return _IBM_KEEP_COLUMNS
    if source == IEEE_CIS:
        return _IEEE_KEEP_COLUMNS
    raise _unsupported(source)


def sample_frame(frame: pd.DataFrame, source: str, n_rows: int, seed: int) -> pd.DataFrame:
    """Return a seeded, label-stratified subsample (~n_rows) of the frame for fast iteration.

    Sampling per class preserves the (rare) laundering base rate; a seeded numpy Generator makes
    it deterministic. Returns the frame unchanged when n_rows is not smaller than the frame.
    """
    if source == IBM_AML:
        labels = frame[_COL_IS_LAUNDERING].to_numpy()
    elif source == IEEE_CIS:
        labels = frame[_COL_IEEE_LABEL].to_numpy()
    else:
        raise _unsupported(source)
    if n_rows >= len(frame):
        return frame.reset_index(drop=True)
    if n_rows < 1:
        raise ValueError("sample rows must be at least 1")
    generator = np.random.default_rng(seed)
    fraction = n_rows / len(frame)
    keep: list[int] = []
    for value in np.unique(labels):
        positions = np.where(labels == value)[0]
        take = min(len(positions), max(1, round(len(positions) * fraction)))
        keep.extend(generator.choice(positions, size=take, replace=False).tolist())
    keep.sort()
    return frame.iloc[keep].reset_index(drop=True)


def demo_agency_index(bank: str, agency_count: int = _DEFAULT_DEMO_AGENCIES) -> int:
    """Map a source bank to one of `agency_count` demo agencies (deterministic tenancy spread).

    The demo-ingest path binds this partition to real Agency rows so ingested real data exercises
    multi-tenant isolation; the TRAINING matrix stays agency-agnostic (global training, ADR-015).
    """
    if agency_count < 1:
        raise ValueError("agency_count must be >= 1")
    digest = sum(bank.strip().encode("utf-8"))
    return digest % agency_count


def _required_row_text(row: Mapping[str, Any], column: str) -> str:
    """Return a non-empty IBM column without echoing its value in validation failures."""
    value = row.get(column)
    if value is None or str(value).strip() == "":
        raise SchemaValidationError(column, "required")
    return str(value).strip()


def _ibm_timestamp(value: str) -> pd.Timestamp:
    """Parse one IBM timestamp as UTC, raising a value-free schema error on failure."""
    try:
        parsed = pd.to_datetime(value, utc=True)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(_COL_TIMESTAMP, "invalid_datetime") from exc
    if pd.isna(parsed):
        raise SchemaValidationError(_COL_TIMESTAMP, "invalid_datetime")
    return pd.Timestamp(parsed)


def map_ibm_demo_row(
    row: Mapping[str, Any], row_index: int, agency_count: int = _DEFAULT_DEMO_AGENCIES
) -> IbmDemoTransaction:
    """Map one public IBM row into the canonical masked-ingest path and a tenant partition."""
    from_bank = _required_row_text(row, _COL_FROM_BANK)
    from_account = _required_row_text(row, _COL_FROM_ACCOUNT)
    to_bank = _required_row_text(row, _COL_TO_BANK)
    to_account = _required_row_text(row, _COL_TO_ACCOUNT)
    timestamp = _required_row_text(row, _COL_TIMESTAMP)
    amount = _required_row_text(row, _COL_AMOUNT_PAID)
    payment_currency = _required_row_text(row, _COL_PAYMENT_CURRENCY)
    payment_format = _required_row_text(row, _COL_PAYMENT_FORMAT)
    digest_input = "\x1f".join(
        (str(row_index), timestamp, from_bank, from_account, to_bank, to_account, amount)
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    canonical = build_canonical(
        external_id=f"{_DEMO_EXTERNAL_ID_PREFIX}-{digest[:_DEMO_EXTERNAL_ID_DIGEST_LENGTH]}",
        amount=amount,
        currency=ibm_currency(payment_currency),
        occurred_at=_ibm_timestamp(timestamp).to_pydatetime(),
        origin_account=ibm_account_key(from_bank, from_account),
        dest_account=ibm_account_key(to_bank, to_account),
        channel=ibm_channel(payment_format),
        country=ibm_country(payment_currency),
        features={"dataset_source": IBM_AML},
    )
    return IbmDemoTransaction(
        agency_index=demo_agency_index(from_bank, agency_count),
        canonical=canonical,
    )


@dataclass(frozen=True)
class _AnchorSpec:
    """One laundering anchor account: its tenant and the time neighborhood it claims."""

    tenant: int
    window_start: float
    window_end: float


def _ibm_chunks(csv_path: Path) -> Iterator[pd.DataFrame]:
    """Yield string-typed chunks of the IBM CSV (memory-safe scan of the full file)."""
    yield from pd.read_csv(
        csv_path,
        usecols=list(_IBM_KEEP_COLUMNS),
        dtype=str,
        chunksize=_CASE_PACK_CHUNK_ROWS,
    )


def _epoch_seconds(value: str) -> float:
    """Parse one IBM timestamp string to float epoch seconds (UTC)."""
    return float(_ibm_timestamp(value).timestamp())


def _select_anchors(
    csv_path: Path,
    anchor_budget: int,
    agency_count: int,
    tenant_weights: tuple[int, ...] = _DEFAULT_TENANT_WEIGHTS,
) -> dict[str, _AnchorSpec]:
    """Pick the earliest distinct laundering origin accounts and assign their tenants.

    Deterministic: laundering rows are ordered by (timestamp, file position); `tenant_weights`
    cycles tenants so the primary (batch-scored) tenant receives the caller's chosen share of
    laundering neighborhoods (the default is the study's 60/20/20 spread).
    """
    laundering: list[tuple[float, int, str]] = []
    for chunk in _ibm_chunks(csv_path):
        hits = chunk[chunk[_COL_IS_LAUNDERING] == _LAUNDERING_LABEL]
        # read_csv(chunksize=...) keeps one global RangeIndex across chunks, so `position`
        # is already the file-order row index (the deterministic tie-break).
        for position, row in zip(hits.index, hits.to_dict(orient="records"), strict=True):
            key = ibm_account_key(str(row[_COL_FROM_BANK]), str(row[_COL_FROM_ACCOUNT]))
            laundering.append((_epoch_seconds(str(row[_COL_TIMESTAMP])), int(position), key))
    if not laundering:
        raise ValueError(
            "case pack found no laundering ground truth in the dataset file — "
            "verify the fetched variant is HI-Small_Trans.csv"
        )
    if not tenant_weights:
        raise ValueError("tenant_weights must contain at least one partition index")
    laundering.sort(key=lambda item: (item[0], item[1], item[2]))
    anchors: dict[str, _AnchorSpec] = {}
    for moment, _, key in laundering:
        if key in anchors:
            continue
        tenant = tenant_weights[len(anchors) % len(tenant_weights)] % agency_count
        anchors[key] = _AnchorSpec(
            tenant=tenant,
            window_start=moment - _NEIGHBORHOOD_WINDOW_SECONDS,
            window_end=moment + _NEIGHBORHOOD_WINDOW_SECONDS,
        )
        if len(anchors) >= anchor_budget:
            break
    return anchors


def load_ibm_case_pack(
    paths: DatasetPaths,
    *,
    rows: int,
    agency_count: int = _DEFAULT_DEMO_AGENCIES,
    tenant_weights: tuple[int, ...] = _DEFAULT_TENANT_WEIGHTS,
) -> list[IbmDemoTransaction]:
    """Build the deterministic demo case pack: laundering neighborhoods + benign controls.

    Replaces the old CSV prefix (which contained zero laundering context). Selection is a pure
    function of the file + parameters: anchor accounts are the earliest distinct laundering
    senders; each contributes its complete account/time neighborhood (rows where it is sender OR
    receiver inside ±3 days of its first laundering row, capped); benign stride-sampled controls
    (never touching an anchor) fill the remaining budget. A neighborhood stays inside ONE tenant
    so the served history windows match training. The public label steers selection only — it is
    never persisted, logged, or converted into an alert.
    """
    if rows < 1:
        raise ValueError("case pack rows must be at least 1")
    csv_path = Path(paths.directory) / paths.files[0].name
    total_rows = paths.files[0].row_count
    anchor_budget = max(_ANCHOR_MIN, rows // _ANCHOR_ROW_DIVISOR)
    anchors = _select_anchors(csv_path, anchor_budget, agency_count, tenant_weights)

    stride = max(1, total_rows // max(1, rows))
    benign_cap = rows * _BENIGN_OVERSAMPLE
    neighborhoods: dict[str, list[tuple[int, dict[str, Any]]]] = {key: [] for key in anchors}
    benign: list[tuple[int, dict[str, Any]]] = []
    offset = 0
    for chunk in _ibm_chunks(csv_path):
        records = chunk.to_dict(orient="records")
        for position, row in enumerate(records):
            row_index = offset + position
            okey = ibm_account_key(str(row[_COL_FROM_BANK]), str(row[_COL_FROM_ACCOUNT]))
            dkey = ibm_account_key(str(row[_COL_TO_BANK]), str(row[_COL_TO_ACCOUNT]))
            anchor_key = okey if okey in anchors else (dkey if dkey in anchors else None)
            if anchor_key is not None:
                bucket = neighborhoods[anchor_key]
                if len(bucket) < _NEIGHBORHOOD_MAX_ROWS:
                    spec = anchors[anchor_key]
                    moment = _epoch_seconds(str(row[_COL_TIMESTAMP]))
                    is_laundering = str(row[_COL_IS_LAUNDERING]) == _LAUNDERING_LABEL
                    # Laundering rows of the anchor always belong; context rows must fall
                    # inside the anchor's time neighborhood.
                    if is_laundering or spec.window_start <= moment <= spec.window_end:
                        bucket.append((row_index, row))
                continue
            if (
                str(row[_COL_IS_LAUNDERING]) == _BENIGN_LABEL
                and row_index % stride == 0
                and len(benign) < benign_cap
            ):
                benign.append((row_index, row))
        offset += len(records)

    pack: list[tuple[int, dict[str, Any], int]] = []
    used: set[int] = set()
    for key, spec in anchors.items():
        bucket = neighborhoods[key]
        if not bucket:
            continue
        if pack and len(pack) + len(bucket) > rows:
            break  # keep whole neighborhoods; benign controls fill the remainder.
        for row_index, row in bucket:
            if row_index in used or len(pack) >= rows:
                continue
            used.add(row_index)
            pack.append((row_index, row, spec.tenant))
    for row_index, row in benign:
        if len(pack) >= rows:
            break
        if row_index in used:
            continue
        used.add(row_index)
        tenant = demo_agency_index(str(row[_COL_FROM_BANK]), agency_count)
        pack.append((row_index, row, tenant))

    pack.sort(key=lambda item: item[0])
    transactions: list[IbmDemoTransaction] = []
    skipped = 0
    for row_index, row, tenant in pack:
        try:
            mapped = map_ibm_demo_row(row, row_index, agency_count)
        except SchemaValidationError:
            # Un-ingestable source row (e.g. sub-cent dust that rounds to zero cents) — the
            # canonical boundary would reject it, so the pack skips it deterministically.
            skipped += 1
            continue
        transactions.append(IbmDemoTransaction(agency_index=tenant, canonical=mapped.canonical))
    if skipped:
        print(f">> case pack: skipped {skipped} un-ingestable source rows (canonical rejects)")
    return transactions
