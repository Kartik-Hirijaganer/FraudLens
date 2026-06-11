"""Summary: Catalog schema and loader for the non-secret LLM capability and trust
registry. The catalog is keyed provider -> model id, while public references use
the first-slash provider/model-id convention.

Key classes:
- Kind: Model operation kind.
- Modality: Model input/output modality.
- Speed: Relative latency bucket for selection.
- Intelligence: Relative capability bucket for selection.
- Lifecycle: Trust lifecycle for catalog entries.
- GenerationParams: Bounded provider parameter allowlist.
- ModelCard: Capability, pricing, and trust metadata for one model.
- Catalog: Validated catalog wrapper with lookup and selection helpers.

Key functions:
- load_catalog: Load and validate a catalog YAML file.

Notes:
- ModelCard allows unknown descriptive fields, but GenerationParams forbids them.
"""

from __future__ import annotations

import math
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from fraudlens_llm.exceptions import CatalogError, ModelNotFoundError


class Kind(StrEnum):
    """Supported v1 model operation kinds."""

    CHAT = "chat"
    EMBED = "embed"


class Modality(StrEnum):
    """Supported catalog modality values."""

    TEXT = "text"
    VISION = "vision"
    AUDIO = "audio"


class Speed(StrEnum):
    """Relative model speed buckets."""

    VERY_FAST = "very_fast"
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class Intelligence(StrEnum):
    """Relative model intelligence buckets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HIGHEST = "highest"


class Lifecycle(StrEnum):
    """Catalog trust lifecycle values."""

    GA = "ga"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"
    RETIRED = "retired"
    REFERENCE = "reference"


class GenerationParams(BaseModel):
    """Bounded allowlist for generation and embedding parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2, description="Sampling temperature.")
    max_tokens: int | None = Field(default=None, ge=1, description="Maximum output tokens.")
    top_p: float | None = Field(default=None, ge=0, le=1, description="Nucleus sampling value.")
    stop: list[str] | None = Field(
        default=None, max_length=8, description="Stop sequences for text generation."
    )
    dimensions: int | None = Field(default=None, ge=1, description="Embedding dimensions.")
    response_format: str | None = Field(
        default=None, min_length=1, description="Provider response format hint."
    )
    language: str | None = Field(default=None, min_length=1, description="Language hint.")
    seed: int | None = Field(default=None, description="Deterministic seed where supported.")
    frequency_penalty: float | None = Field(
        default=None, ge=-2, le=2, description="Frequency penalty."
    )
    presence_penalty: float | None = Field(
        default=None, ge=-2, le=2, description="Presence penalty."
    )
    reasoning_effort: str | None = Field(
        default=None, min_length=1, description="Provider reasoning-effort hint."
    )


class ModelCard(BaseModel):
    """Capability, pricing, and trust metadata for one catalog model."""

    model_config = ConfigDict(frozen=True, extra="allow")

    kind: Kind = Field(..., description="Model operation kind.")
    context_window: int = Field(..., ge=0, description="Maximum context window.")
    max_token_output: int | None = Field(
        default=None, ge=0, description="Maximum generated token output."
    )
    modality: list[Modality] = Field(
        default_factory=lambda: [Modality.TEXT], description="Supported modalities."
    )
    knowledge_cutoff: date | None = Field(default=None, description="Knowledge cutoff date.")
    default_params: GenerationParams = Field(
        default_factory=GenerationParams, description="Default generation parameters."
    )
    input_price_per_million: float | None = Field(
        default=None, ge=0, description="Input price per million tokens."
    )
    output_price_per_million: float | None = Field(
        default=None, ge=0, description="Output price per million tokens."
    )
    input_price_per_minute: float | None = Field(
        default=None, ge=0, description="Input price per audio minute."
    )
    speed: Speed | None = Field(default=None, description="Relative speed bucket.")
    reasoning_capable: bool = Field(default=False, description="Whether reasoning is supported.")
    intelligence: Intelligence | None = Field(default=None, description="Intelligence bucket.")
    max_audio_duration: int | None = Field(
        default=None, ge=0, description="Maximum audio duration in seconds."
    )
    supported_languages: str | list[str] | None = Field(
        default=None, description="Supported language metadata."
    )
    features: list[str] = Field(default_factory=list, description="Descriptive feature list.")
    source_url: str | None = Field(default=None, description="Source used to verify metadata.")
    verified_at: date | None = Field(default=None, description="Date metadata was verified.")
    lifecycle: Lifecycle = Field(default=Lifecycle.REFERENCE, description="Catalog lifecycle.")
    callable: bool = Field(default=False, description="Whether v1 may call this model.")
    pricing_basis: Literal["per_million_tokens", "per_minute"] | None = Field(
        default=None, description="How pricing fields should be interpreted."
    )


_CatalogData = dict[str, dict[str, ModelCard]]
_CATALOG_ADAPTER: TypeAdapter[_CatalogData] = TypeAdapter(_CatalogData)
_INTELLIGENCE_RANK: dict[Intelligence, int] = {
    Intelligence.LOW: 1,
    Intelligence.MEDIUM: 2,
    Intelligence.HIGH: 3,
    Intelligence.HIGHEST: 4,
}
_SELECTABLE_LIFECYCLES = {Lifecycle.GA, Lifecycle.PREVIEW}


class Catalog(BaseModel):
    """Validated catalog wrapper with lookup and requirement-based selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    providers: _CatalogData = Field(..., description="Catalog entries by provider and model id.")

    def get(self, ref: str) -> tuple[str, str, ModelCard]:
        """Return provider, model id, and card for a provider/model-id reference."""
        provider, separator, model_id = ref.partition("/")
        if not separator or not provider or not model_id:
            raise ModelNotFoundError(f"Invalid model reference '{ref}'")
        provider_models = self.providers.get(provider)
        if provider_models is None or model_id not in provider_models:
            raise ModelNotFoundError(f"Model reference '{ref}' was not found")
        return provider, model_id, provider_models[model_id]

    def select(  # noqa: PLR0913 - plan requires requirement-based keyword filters.
        self,
        *,
        kind: Kind | None = None,
        modality: Modality | None = None,
        min_intelligence: Intelligence | None = None,
        max_input_price: float | None = None,
        reasoning_capable: bool | None = None,
        speed: Speed | None = None,
        provider: str | None = None,
        include_unverified: bool = False,
    ) -> list[str]:
        """Select model references by requirements, ranked by intelligence then cost."""
        matches: list[tuple[str, ModelCard]] = []
        for provider_name, model_cards in self.providers.items():
            if provider is not None and provider_name != provider:
                continue
            for model_id, card in model_cards.items():
                if not include_unverified and not _is_default_selectable(card):
                    continue
                if kind is not None and card.kind != kind:
                    continue
                if modality is not None and modality not in card.modality:
                    continue
                if min_intelligence is not None and not _meets_intelligence(card, min_intelligence):
                    continue
                if (
                    max_input_price is not None
                    and card.input_price_per_million is not None
                    and card.input_price_per_million > max_input_price
                ):
                    continue
                if reasoning_capable is not None and card.reasoning_capable != reasoning_capable:
                    continue
                if speed is not None and card.speed != speed:
                    continue
                matches.append((f"{provider_name}/{model_id}", card))
        matches.sort(key=_selection_rank)
        return [ref for ref, _card in matches]


def _is_default_selectable(card: ModelCard) -> bool:
    """Return whether a model is selectable by default."""
    return (
        card.callable
        and card.lifecycle in _SELECTABLE_LIFECYCLES
        and card.verified_at is not None
        and set(card.modality) != {Modality.AUDIO}
    )


def _meets_intelligence(card: ModelCard, minimum: Intelligence) -> bool:
    """Return whether a card meets the minimum intelligence bucket."""
    if card.intelligence is None:
        return False
    return _INTELLIGENCE_RANK[card.intelligence] >= _INTELLIGENCE_RANK[minimum]


def _selection_rank(item: tuple[str, ModelCard]) -> tuple[int, float, str]:
    """Rank higher intelligence first, then cheaper input price, then stable ref."""
    ref, card = item
    intelligence_rank = (
        _INTELLIGENCE_RANK[card.intelligence] if card.intelligence is not None else 0
    )
    input_price = card.input_price_per_million
    return (-intelligence_rank, input_price if input_price is not None else math.inf, ref)


def load_catalog(path: str | Path) -> Catalog:
    """Load and validate catalog YAML, wrapping parser/validation errors."""
    catalog_path = Path(path)
    try:
        raw: Any = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        data = _CATALOG_ADAPTER.validate_python(raw)
        return Catalog(providers=data)
    except (OSError, TypeError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"Failed to load LLM catalog from {catalog_path}") from exc
