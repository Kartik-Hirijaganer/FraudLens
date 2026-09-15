"""Summary: Atomic, canonical artifact persistence shared by reproducible studies.

Key classes:
- (none)

Key functions:
- canonical_json: serialize a model or JSON value deterministically.
- atomic_write_text: replace one text artifact atomically.
- atomic_write_model: serialize and atomically replace one Pydantic artifact.
- install_bound_artifacts: install a set of artifacts with rollback on failure.

Notes:
- Bound installs restore every pre-existing file if any replacement fails.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def canonical_json(value: BaseModel | Any) -> str:
    """Return deterministic, indented JSON with aliases applied to Pydantic models."""
    payload = (
        value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value
    )
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def atomic_write_text(path: Path, content: str) -> None:
    """Write text beside its destination and atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.staging")
    staging.write_text(content, encoding="utf-8")
    try:
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def atomic_write_model(path: Path, artifact: BaseModel) -> None:
    """Serialize a Pydantic artifact canonically and replace its destination atomically."""
    atomic_write_text(path, canonical_json(artifact))


def install_bound_artifacts(
    artifacts: Mapping[Path, str], *, validate: Callable[[], None] | None = None
) -> None:
    """Install related text artifacts and restore the previous set if any write fails."""
    previous: dict[Path, bytes | None] = {
        path: path.read_bytes() if path.exists() else None for path in artifacts
    }
    try:
        for path, content in artifacts.items():
            atomic_write_text(path, content)
        if validate is not None:
            validate()
    except Exception:
        for path, prior_content in previous.items():
            if prior_content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(prior_content)
        raise
