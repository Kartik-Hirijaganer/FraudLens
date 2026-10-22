"""Summary: Bounded RFC-style CSV parsing with value-free structural failures.

Key classes:
- CsvRecord: a bounded row and its starting physical line number.
- CsvDocument: parsed records with exact-file synthetic provenance.

Key functions:
- parse_csv: enforce field, column, row, encoding, quoting and header limits while parsing.

Notes:
- Structural failures reject the entire file before persistence; semantic errors are per row.
- Field buffers are checked on each character; arbitrary headers never enter error messages.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_backend.models.errors import AppError

TRANSACTION_COLUMNS = (
    "externalId",
    "amount",
    "currency",
    "occurredAt",
    "originAccount",
    "destAccount",
    "channel",
    "country",
)


class CsvRecord(BaseModel):
    """A parsed row; field names have already passed the closed header contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    row_number: int = Field(..., ge=2, description="Starting physical CSV line, including header.")
    values: dict[str, str] = Field(..., description="Validated headers mapped to bounded cells.")


class CsvDocument(BaseModel):
    """Only a byte-identical bundled synthetic fixture earns trusted provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    rows: list[CsvRecord] = Field(..., description="Structurally validated CSV records.")
    trusted_synthetic: bool = Field(..., description="Exact match to the server-owned demo CSV.")


def _records(  # noqa: PLR0912 - one bounded CSV grammar state machine.
    text: str, max_columns: int, max_field_chars: int
) -> Iterator[tuple[int, list[str]]]:
    row: list[str] = []
    cell: list[str] = []
    state = "start"
    line = start = 1
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\x00":
            raise AppError("invalid_csv")
        if state == "quoted":
            if char == '"':
                state = "closed"
            else:
                cell.append(char)
                line += char == "\n" or (char == "\r" and text[index : index + 1] != "\n")
        elif char == '"':
            if state == "start":
                state = "quoted"
            elif state == "closed":
                cell.append(char)
                state = "quoted"
            else:
                raise AppError("invalid_csv")
        elif char in ",\r\n":
            row.append("".join(cell))
            cell = []
            state = "start"
            if len(row) > max_columns or (char == "," and len(row) == max_columns):
                raise AppError("csv_column_limit")
            if char != ",":
                if char == "\r" and index < len(text) and text[index] == "\n":
                    index += 1
                yield start, row
                row = []
                line += 1
                start = line
        elif state == "closed":
            raise AppError("invalid_csv")
        else:
            cell.append(char)
            state = "plain"
        if len(cell) > max_field_chars:
            raise AppError("csv_field_limit")
    if state == "quoted":
        raise AppError("invalid_csv")
    if row or cell or state != "start":
        row.append("".join(cell))
        yield start, row


def parse_csv(
    body: bytes, *, max_rows: int, max_columns: int, max_field_chars: int
) -> list[CsvRecord]:
    """Parse a byte-bounded file with strict UTF-8 and a closed, unique header set."""
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise AppError("invalid_csv_encoding") from exc
    records = _records(text, max_columns, max_field_chars)
    header = next(records, (0, []))[1]
    if len(header) != len(set(header)) or set(header) != set(TRANSACTION_COLUMNS):
        raise AppError("invalid_csv_headers")
    rows: list[CsvRecord] = []
    for row_number, values in records:
        if len(rows) >= max_rows:
            raise AppError("too_many_rows")
        if len(values) != len(header):
            raise AppError("invalid_csv_width")
        rows.append(CsvRecord(row_number=row_number, values=dict(zip(header, values, strict=True))))
    if not rows:
        raise AppError("empty_payload")
    return rows
