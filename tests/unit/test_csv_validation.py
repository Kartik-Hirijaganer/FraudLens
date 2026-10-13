"""Summary: Bounded CSV grammar, strict facts and value-free rejection contracts.

Key classes:
- (none)

Key functions:
- (none)

Notes:
- All identifiers and adversarial strings are invented synthetic fixtures.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from fraudlens_backend.models.errors import AppError
from fraudlens_backend.services.csv_validation import TRANSACTION_COLUMNS, parse_csv
from fraudlens_backend.services.transaction_ingest import _mapping_to_canonical, _read_csv
from fraudlens_core import SchemaValidationError

_HEADER = ",".join(TRANSACTION_COLUMNS)
_ROW = "DEMO-1,10.00,USD,2026-01-01T00:00:00Z,DEMO-A,DEMO-B,wire,US"


def _parse(text: str | bytes, **limits: int) -> Any:
    return parse_csv(
        text.encode() if isinstance(text, str) else text,
        **({"max_rows": 10, "max_columns": 8, "max_field_chars": 128} | limits),
    )


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_valid_bom_crlf_quotes_and_multiline_keep_physical_row_numbers(newline: str) -> None:
    identifier = f'DEMO{newline}"ONE",x'
    body = (
        "\ufeff"
        + _HEADER
        + newline
        + _ROW.replace("DEMO-1", f'"DEMO{newline}""ONE"",x"')
        + newline
        + _ROW
    )
    rows = _parse(body)
    assert rows[0].row_number == 2 and rows[1].row_number == 4
    assert rows[0].values["externalId"] == identifier
    with pytest.raises(SchemaValidationError, match="invalid_identifier"):
        _mapping_to_canonical(rows[0].values)
    assert _mapping_to_canonical(rows[1].values).amount.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("", "invalid_csv_headers"),
        (_HEADER, "empty_payload"),
        (_HEADER.replace("country", "amount") + "\n" + _ROW, "invalid_csv_headers"),
        (
            _HEADER.replace("country", "synthetic@example.invalid") + "\n" + _ROW,
            "invalid_csv_headers",
        ),
        (_HEADER + "\n" + _ROW + ",extra", "csv_column_limit"),
        (_HEADER + "\n" + _ROW.rsplit(",", 1)[0], "invalid_csv_width"),
        (_HEADER + '\n"unfinished', "invalid_csv"),
        (_HEADER + "\n" + _ROW.replace("DEMO-1", 'D"EMO'), "invalid_csv"),
        (_HEADER + "\n" + _ROW.replace("DEMO-1", '"DEMO"x'), "invalid_csv"),
        (_HEADER + "\n" + _ROW.replace("DEMO-1", "\x00"), "invalid_csv"),
        ((_HEADER + "\n").encode() + b"\xff", "invalid_csv_encoding"),
        (_HEADER + "\n" + _ROW + "\n\n", "invalid_csv_width"),
    ],
)
def test_structural_errors_never_echo_values(body: str | bytes, code: str) -> None:
    with pytest.raises(AppError) as raised:
        _parse(body)
    assert raised.value.code == code
    assert "synthetic@example.invalid" not in str(raised.value)


@pytest.mark.parametrize(
    ("limits", "body", "code"),
    [
        ({"max_rows": 1}, _HEADER + "\n" + _ROW + "\n" + _ROW, "too_many_rows"),
        ({"max_columns": 7}, _HEADER, "csv_column_limit"),
        ({}, _HEADER + "\n" + _ROW.replace("DEMO-1", "X" * 129), "csv_field_limit"),
        ({}, _HEADER + "\n" + _ROW.replace("DEMO-1", '"' + "X" * 129 + '"'), "csv_field_limit"),
    ],
)
def test_limits_apply_during_parse(limits: dict[str, int], body: str, code: str) -> None:
    with pytest.raises(AppError) as raised:
        _parse(body, **limits)
    assert raised.value.code == code


async def test_chunked_body_stops_reading_at_byte_limit_without_content_length() -> None:
    reads = 0

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        assert reads <= 2, "reader consumed beyond its byte allowance"
        return {"type": "http.request", "body": b"x" * 10, "more_body": True}

    request = Request({"type": "http", "headers": [(b"content-type", b"text/csv")]}, receive)
    with pytest.raises(AppError, match="payload_too_large"):
        await _read_csv(request, 15, 10)
    assert reads == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", "10.005"),
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", "1e500"),
        ("amount", "10000000000000000"),
        ("amount", "0"),
        ("amount", "-10"),
        ("amount", "bad"),
        ("currency", "ZZZ"),
        ("country", "XX"),
        ("channel", "ignore_previous_instructions"),
        ("occurredAt", "2026-01-01T00:00:00"),
        ("occurredAt", "2999-01-01T00:00:00Z"),
        ("occurredAt", "not-a-date"),
        ("originAccount", "Alice Example"),
        ("destAccount", "synthetic@example.invalid"),
        ("externalId", "=PROMPT()"),
        ("externalId", " "),
        ("externalId", 123),
        ("channel", 12),
        ("features", {"source": "synthetic-generator"}),
        ("features", "bad"),
        ("synthetic@example.invalid", "hidden"),
    ],
)
def test_shared_upload_validation_rejects_unsafe_or_lossy_facts(field: str, value: Any) -> None:
    row = _parse(_HEADER + "\n" + _ROW)[0].values
    with pytest.raises(SchemaValidationError) as raised:
        _mapping_to_canonical(row | {field: value})
    assert "synthetic@example.invalid" not in str(raised.value)
