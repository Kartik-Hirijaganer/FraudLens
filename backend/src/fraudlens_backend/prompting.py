"""Summary: Shared loader for versioned, content-hashed prompt templates.
Prompt files use YAML front matter followed by a static instruction body. This
module centralizes parsing and provenance so SAR and agent prompts cannot drift.

Key classes:
- PromptMeta: validated version and description front matter.
- VersionedPrompt: immutable prompt body plus version and SHA-256 provenance.

Key functions:
- split_front_matter: parse and validate a fenced prompt document.
- load_versioned_prompt: load one prompt file and compute stable provenance.

Notes:
- The hash covers the exact UTF-8 file bytes, including front matter and whitespace.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field

_FRONT_MATTER_FENCE = "---"
PromptMetaT = TypeVar("PromptMetaT", bound="PromptMeta")


class PromptMeta(BaseModel):
    """Validated metadata shared by every versioned prompt template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(..., min_length=1, description="Semantic prompt version.")
    description: str = Field(..., min_length=1, description="Purpose of this prompt version.")


class VersionedPrompt(BaseModel):
    """Loaded static prompt text with stable version and exact-file hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(..., min_length=1, description="Prompt template file identifier.")
    meta: PromptMeta = Field(..., description="Validated prompt front matter.")
    system_text: str = Field(..., min_length=1, description="Static system instruction body.")
    prompt_version: str = Field(..., min_length=1, description="Template id and semantic version.")
    prompt_hash: str = Field(..., min_length=1, description="SHA-256 of the exact prompt file.")


def split_front_matter(
    raw: str,
    *,
    meta_type: type[PromptMetaT],
    prompt_label: str = "Prompt",
) -> tuple[PromptMetaT, str]:
    """Split a fenced prompt into validated front matter and instruction body."""
    text = raw.lstrip()
    if not text.startswith(_FRONT_MATTER_FENCE):
        raise ValueError(f"{prompt_label} prompt template is missing its '---' front matter")
    closing = text.find(f"\n{_FRONT_MATTER_FENCE}", len(_FRONT_MATTER_FENCE))
    if closing == -1:
        raise ValueError(f"{prompt_label} prompt template front matter is not closed with '---'")
    front = text[len(_FRONT_MATTER_FENCE) : closing]
    body = text[closing + len(_FRONT_MATTER_FENCE) + 1 :]
    parsed = yaml.safe_load(front) or {}
    return meta_type.model_validate(parsed), body


def load_versioned_prompt(
    path: Path,
    *,
    template_id: str,
    meta_type: type[PromptMetaT],
    prompt_label: str = "Prompt",
) -> VersionedPrompt:
    """Load one prompt file and return its static text plus stable provenance."""
    raw_bytes = path.read_bytes()
    raw = raw_bytes.decode("utf-8")
    meta, body = split_front_matter(raw, meta_type=meta_type, prompt_label=prompt_label)
    return VersionedPrompt(
        template_id=template_id,
        meta=meta,
        system_text=body.strip(),
        prompt_version=f"{template_id}@{meta.version}",
        prompt_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )
