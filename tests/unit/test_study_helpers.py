"""Shared study helper tests for atomic rollback, hashes, checkpoints, and URL origins."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from lib.study.artifacts import install_bound_artifacts
from lib.study.binding import (
    derive_run_id,
    load_checkpoint_or_initialize,
    sha256_hex,
    validate_hash_binding,
)
from lib.study.urls import validate_origin_url


class _Checkpoint(BaseModel):
    value: int


def test_hash_binding_and_run_identity_fail_closed() -> None:
    digest = sha256_hex("identity")
    validate_hash_binding("identity", digest, artifact="config")
    assert derive_run_id("study", "identity", digest_length=8) == f"study-{digest[:8]}"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_hash_binding("changed", digest, artifact="config")
    with pytest.raises(ValueError, match="prefix"):
        derive_run_id("", "identity")


def test_checkpoint_loads_existing_or_initializes(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    assert load_checkpoint_or_initialize(path, _Checkpoint, lambda: _Checkpoint(value=1)).value == 1
    path.write_text('{"value": 2}', encoding="utf-8")
    assert load_checkpoint_or_initialize(path, _Checkpoint, lambda: _Checkpoint(value=1)).value == 2


def test_bound_install_restores_prior_files_when_validation_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("original", encoding="utf-8")

    def reject() -> None:
        raise ValueError("binding mismatch")

    with pytest.raises(ValueError, match="binding mismatch"):
        install_bound_artifacts({first: "changed", second: "created"}, validate=reject)
    assert first.read_text(encoding="utf-8") == "original"
    assert not second.exists()


def test_origin_validation_allows_only_https_or_named_loopback() -> None:
    assert validate_origin_url("https://example.test/") == "https://example.test"
    assert (
        validate_origin_url("http://localhost:8000", allow_http_hosts=("localhost",))
        == "http://localhost:8000"
    )
    for value in ("http://example.test", "https://user@example.test", "https://example.test/path"):
        with pytest.raises(ValueError, match="origin"):
            validate_origin_url(value)
