"""Unit tests for the no-hardcoding guard (URLs / IPs / model-ids in source)."""

from __future__ import annotations

from pathlib import Path

from check_no_hardcoding import iter_offences, main


def _offences(tmp_path: Path, body: str) -> list[str]:
    """Write body to a temp .py file and return the reasons iter_offences yields."""
    path = tmp_path / "sample.py"
    path.write_text(body, encoding="utf-8")
    return [reason for _p, _line, _col, reason in iter_offences(path)]


def test_flags_hardcoded_url(tmp_path: Path) -> None:
    reasons = _offences(tmp_path, 'x = "https://api.example.com/v1"\n')
    assert any("URL" in r for r in reasons)


def test_flags_ipv4_literal(tmp_path: Path) -> None:
    reasons = _offences(tmp_path, 'host = "10.0.0.5"\n')
    assert any("IP" in r for r in reasons)


def test_flags_model_id(tmp_path: Path) -> None:
    reasons = _offences(tmp_path, 'model = "claude-haiku-4-5"\n')
    assert any("model id" in r for r in reasons)


def test_ignores_bare_scheme_string(tmp_path: Path) -> None:
    # The bare scheme (used in validators) has no host char after "//", so it is fine.
    assert _offences(tmp_path, 'assert value.startswith("https://")\n') == []


def test_ignores_fstring_built_from_parts(tmp_path: Path) -> None:
    # The literal fragment is only the scheme; the host is interpolated → not flagged.
    assert _offences(tmp_path, 'url = f"http://{host}:{port}/healthz"\n') == []


def test_ignores_urls_in_docstrings(tmp_path: Path) -> None:
    body = '"""See https://docs.example.com for details."""\nx = 1\n'
    assert _offences(tmp_path, body) == []


def test_respects_allow_hardcoded_suppression(tmp_path: Path) -> None:
    assert _offences(tmp_path, 'x = "https://api.example.com"  # allow-hardcoded\n') == []


def test_main_passes_on_the_current_repo() -> None:
    # The repo is kept free of hardcoded URLs/IPs/model-ids (mirrors `make ci`).
    assert main() == 0
