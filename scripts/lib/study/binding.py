"""Summary: Hash and checkpoint binding primitives for deterministic, resumable studies.

Key classes:
- (none)

Key functions:
- sha256_hex: return a lowercase SHA-256 digest.
- derive_run_id: derive a prefixed run id from canonical identity bytes.
- validate_hash_binding: compare a claimed digest with observed content.
- load_checkpoint_or_initialize: parse existing state or construct initial state.
- model_family: extract the provider family from a routed model reference.

Notes:
- Binding failures expose artifact labels, never artifact contents.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

CheckpointT = TypeVar("CheckpointT", bound=BaseModel)
_SHA256_HEX_LENGTH = 64
_MODEL_REFERENCE_PARTS = 3


def sha256_hex(content: bytes | str) -> str:
    """Return the lowercase SHA-256 digest of bytes or UTF-8 text."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def derive_run_id(prefix: str, identity: bytes | str, *, digest_length: int = 16) -> str:
    """Derive a stable run id from a non-empty prefix and canonical identity."""
    if not prefix or digest_length <= 0 or digest_length > _SHA256_HEX_LENGTH:
        raise ValueError("run-id prefix and digest length must be valid")
    return f"{prefix}-{sha256_hex(identity)[:digest_length]}"


def validate_hash_binding(content: bytes | str, expected_sha256: str, *, artifact: str) -> None:
    """Reject content whose exact bytes do not match the claimed lowercase digest."""
    if sha256_hex(content) != expected_sha256:
        raise ValueError(f"{artifact} SHA-256 binding does not match")


def load_checkpoint_or_initialize(
    path: Path,
    model: type[CheckpointT],
    initialize: Callable[[], CheckpointT],
) -> CheckpointT:
    """Parse existing checkpoint JSON or return a newly initialized validated model."""
    if not path.exists():
        return initialize()
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def model_family(model_ref: str) -> str:
    """Return the provider-family segment from a router/family/model reference."""
    parts = model_ref.split("/")
    if len(parts) < _MODEL_REFERENCE_PARTS or any(not part for part in parts):
        raise ValueError("model references must include router, family, and model")
    return parts[1]
