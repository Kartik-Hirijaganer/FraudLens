"""Summary: Bounded account-window aggregation over DuckDB's canonical event timeline.

Key classes:
- WindowState: one account's most-recent-100 event state and constant-time aggregates.
- WindowWriter: bounded Arrow-to-Parquet writer for origin or destination markers.

Key functions:
- materialize_feature_windows: stream a sorted DuckDB timeline into two aggregate files.

Notes:
- Markers precede same-timestamp events, enforcing strict [t-24h, t) semantics.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

_EVENT_ROLE = 0
_ORIGIN_ROLE = 1
_DESTINATION_ROLE = 2
_NO_PRIOR_SECONDS = 86_400.0
_SECONDS_PER_HOUR = 3_600


@dataclass(frozen=True)
class _Event:
    """One canonical account event retained only inside the bounded history deque."""

    occurred_epoch: int
    amount: float
    inbound: float
    is_round: float
    country: str
    channel: str


class WindowState:
    """Most-recent bounded history and O(1) numeric/token aggregates for one account."""

    def __init__(self, history_max: int, window_seconds: int) -> None:
        self._history_max = history_max
        self._window_seconds = window_seconds
        self._events: deque[_Event] = deque()
        self._amount = 0.0
        self._inbound = 0.0
        self._inbound_amount = 0.0
        self._round = 0.0
        self._countries: Counter[str] = Counter()
        self._channels: Counter[str] = Counter()

    def _remove(self, event: _Event) -> None:
        self._amount -= event.amount
        self._inbound -= event.inbound
        self._inbound_amount -= event.amount * event.inbound
        self._round -= event.is_round
        self._countries[event.country] -= 1
        self._channels[event.channel] -= 1
        if self._countries[event.country] == 0:
            del self._countries[event.country]
        if self._channels[event.channel] == 0:
            del self._channels[event.channel]

    def trim(self, occurred_epoch: int) -> None:
        """Discard events outside the time window before reading a marker."""
        cutoff = occurred_epoch - self._window_seconds
        while self._events and self._events[0].occurred_epoch < cutoff:
            self._remove(self._events.popleft())

    def append(self, event: _Event) -> None:
        """Append one event and enforce the most-recent history cap."""
        self._events.append(event)
        self._amount += event.amount
        self._inbound += event.inbound
        self._inbound_amount += event.amount * event.inbound
        self._round += event.is_round
        self._countries[event.country] += 1
        self._channels[event.channel] += 1
        while len(self._events) > self._history_max:
            self._remove(self._events.popleft())

    def snapshot(
        self, row_id: int, occurred_epoch: int, country: str, channel: str
    ) -> dict[str, Any]:
        """Return aggregates for a current marker without admitting equal-time events."""
        previous = self._events[-1].occurred_epoch if self._events else None
        return {
            "row_id": row_id,
            "velocity": len(self._events),
            "amount_sum": self._amount,
            "inbound_velocity": self._inbound,
            "inbound_amount": self._inbound_amount,
            "round_count": self._round,
            "seconds_since_prev": (
                occurred_epoch - previous if previous is not None else _NO_PRIOR_SECONDS
            ),
            "distinct_countries": len(self._countries) + (country not in self._countries),
            "distinct_channels": len(self._channels) + (channel not in self._channels),
        }


_WINDOW_SCHEMA = pa.schema(
    [
        ("row_id", pa.int64()),
        ("velocity", pa.int32()),
        ("amount_sum", pa.float64()),
        ("inbound_velocity", pa.float64()),
        ("inbound_amount", pa.float64()),
        ("round_count", pa.float64()),
        ("seconds_since_prev", pa.float64()),
        ("distinct_countries", pa.int32()),
        ("distinct_channels", pa.int32()),
    ]
)


class WindowWriter:
    """Buffer aggregate rows to a compressed Parquet file at a fixed row bound."""

    def __init__(self, path: Path, batch_rows: int) -> None:
        self._path = path
        self._batch_rows = batch_rows
        self._rows: list[dict[str, Any]] = []
        self._writer: pq.ParquetWriter | None = None

    def append(self, row: dict[str, Any]) -> None:
        """Buffer one row and flush when the configured batch bound is reached."""
        self._rows.append(row)
        if len(self._rows) >= self._batch_rows:
            self._flush()

    def _flush(self) -> None:
        if not self._rows:
            return
        table = pa.Table.from_pylist(self._rows, schema=_WINDOW_SCHEMA)
        if self._writer is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = pq.ParquetWriter(self._path, _WINDOW_SCHEMA, compression="zstd")
        self._writer.write_table(table)
        self._rows.clear()

    def close(self) -> None:
        """Flush the final bounded batch and close the Parquet footer."""
        self._flush()
        if self._writer is None:
            raise ValueError("feature timeline emitted no marker rows")
        self._writer.close()


def _timeline_rows(
    connection: duckdb.DuckDBPyConnection, timeline_sql: str, batch_rows: int
) -> Iterator[tuple[Any, ...]]:
    reader = connection.execute(timeline_sql).to_arrow_reader(batch_size=batch_rows)
    for batch in reader:
        columns = batch.to_pydict()
        yield from zip(
            columns["account"],
            columns["occurred_epoch"],
            columns["row_id"],
            columns["role"],
            columns["amount"],
            columns["inbound"],
            columns["is_round"],
            columns["country_token"],
            columns["channel_token"],
            strict=True,
        )


def materialize_feature_windows(  # noqa: PLR0913 - explicit bounded stream contract
    connection: duckdb.DuckDBPyConnection,
    timeline_sql: str,
    origin_path: Path,
    destination_path: Path,
    *,
    history_max: int,
    window_hours: int,
    batch_rows: int,
) -> None:
    """Stream the account-sorted timeline and persist origin/destination window aggregates."""
    for path in (origin_path, destination_path):
        path.unlink(missing_ok=True)
    writers = {
        _ORIGIN_ROLE: WindowWriter(origin_path, batch_rows),
        _DESTINATION_ROLE: WindowWriter(destination_path, batch_rows),
    }
    state = WindowState(history_max, window_hours * _SECONDS_PER_HOUR)
    active_account: str | None = None
    for row in _timeline_rows(connection, timeline_sql, batch_rows):
        account, occurred_epoch, row_id, role, amount, inbound, is_round, country, channel = row
        if account != active_account:
            state = WindowState(history_max, window_hours * _SECONDS_PER_HOUR)
            active_account = account
        state.trim(int(occurred_epoch))
        if role == _EVENT_ROLE:
            state.append(
                _Event(
                    occurred_epoch=int(occurred_epoch),
                    amount=float(amount),
                    inbound=float(inbound),
                    is_round=float(is_round),
                    country=str(country),
                    channel=str(channel),
                )
            )
            continue
        writers[int(role)].append(
            state.snapshot(int(row_id), int(occurred_epoch), str(country), str(channel))
        )
    for writer in writers.values():
        writer.close()
