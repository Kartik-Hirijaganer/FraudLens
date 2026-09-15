"""Summary: Strict prompt, score, checkpoint, and artifact contracts for blind SAR judging.

Key classes:
- JudgePromptTemplate: exact-byte prompt provenance.
- UnsupportedClaim:
- ElementScore:
- CandidateScore:
- JudgeResponse: strict provider response for two blinded candidates.
- ArmJudgeSample:
- JudgeSample:
- JudgeCallReservation:
- JudgeCheckpoint: resumable samples and spend reservations.
- JudgmentArtifact: complete blind-judge output and provenance.
- JudgeClient: minimal guarded generation protocol.

Key functions:
- (none)

Notes:
- Models reject incomplete samples, duplicate reservations, and model-family drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from fraudlens_backend.prompting import PromptMeta, VersionedPrompt, load_versioned_prompt
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import (
    DataClass,
    GenerationParams,
    LlmMessage,
    LlmResult,
    TaskType,
    ToolDefinition,
)
from lib.sar_eval.runner import Arm
from lib.study.binding import model_family as shared_model_family

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
CandidateLabel = Literal["A", "B"]
ElementName = Literal["who", "what", "when", "where", "why"]
_ELEMENTS: tuple[ElementName, ...] = ("who", "what", "when", "where", "why")
_SCENARIO_COUNT = 32


class JudgePromptTemplate(VersionedPrompt):
    """Loaded judge instructions with exact-file version and hash provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def load(cls, template_id: str, *, config_dir: Path | None = None) -> JudgePromptTemplate:
        base = config_dir or find_config_dir()
        loaded = load_versioned_prompt(
            base / "llm" / "prompts" / "sar_eval_judge" / f"{template_id}.md",
            template_id=template_id,
            meta_type=PromptMeta,
            prompt_label="SAR evaluation judge",
        )
        return cls(**loaded.model_dump())


class UnsupportedClaim(BaseModel):
    """One exact candidate span judged unsupported by supplied evidence."""

    model_config = _MODEL_CONFIG

    quoted_span: str = Field(..., min_length=1, description="Shortest exact unsupported span.")
    reason: str = Field(..., min_length=1, description="Concise evidence-gap reason.")


class ElementScore(BaseModel):
    """Presence decision for one FinCEN who/what/when/where/why element."""

    model_config = _MODEL_CONFIG

    element: ElementName = Field(..., description="FinCEN narrative element.")
    present: bool = Field(..., description="Whether the candidate states this element.")
    quoted_span: str | None = Field(default=None, description="Exact supporting span when present.")

    @model_validator(mode="after")
    def _span_matches_decision(self) -> ElementScore:
        if self.present != bool(self.quoted_span):
            raise ValueError("present elements require a span; absent elements require null")
        return self


class CandidateScore(BaseModel):
    """Judge result for one blind candidate label."""

    model_config = _MODEL_CONFIG

    candidate: CandidateLabel = Field(..., description="Blind A/B label.")
    unsupported_claims: tuple[UnsupportedClaim, ...] = Field(
        default=(), description="Material unsupported factual claims."
    )
    elements: tuple[ElementScore, ...] = Field(
        ..., min_length=5, max_length=5, description="Exactly five FinCEN element decisions."
    )

    @model_validator(mode="after")
    def _all_elements_once(self) -> CandidateScore:
        if tuple(item.element for item in self.elements) != _ELEMENTS:
            raise ValueError("elements must be who, what, when, where, why in canonical order")
        return self


class JudgeResponse(BaseModel):
    """Strict structured response containing both blinded candidates exactly once."""

    model_config = _MODEL_CONFIG

    candidates: tuple[CandidateScore, ...] = Field(
        ..., min_length=2, max_length=2, description="Scores for candidates A and B."
    )

    @model_validator(mode="after")
    def _a_and_b(self) -> JudgeResponse:
        if {item.candidate for item in self.candidates} != {"A", "B"}:
            raise ValueError("judge response must score A and B exactly once")
        return self


class ArmJudgeSample(BaseModel):
    """One unblinded arm score retained with quote-level judge evidence."""

    model_config = _MODEL_CONFIG

    arm: Arm = Field(..., description="Workflow restored after blind judging.")
    unsupported_claims: tuple[UnsupportedClaim, ...] = Field(
        default=(), description="Unsupported claims in this sample."
    )
    elements: tuple[ElementScore, ...] = Field(..., description="Five element decisions.")


class JudgeSample(BaseModel):
    """One independent blind paired judgment for a scenario."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key.")
    sample_index: int = Field(..., ge=1, le=3, description="One-based independent sample.")
    presented_order: tuple[Arm, Arm] = Field(..., description="Arm order behind labels A/B.")
    arms: tuple[ArmJudgeSample, ArmJudgeSample] = Field(..., description="Unblinded scores.")

    @model_validator(mode="after")
    def _paired_arms(self) -> JudgeSample:
        expected = {"single_writer", "multi_agent"}
        if set(self.presented_order) != expected or {item.arm for item in self.arms} != expected:
            raise ValueError("judge samples must contain both workflow arms exactly once")
        return self


class JudgeCallReservation(BaseModel):
    """One durable judge-attempt reservation, settled when provider usage is observed."""

    model_config = _MODEL_CONFIG

    scenario_id: str = Field(..., min_length=1, description="Scenario key for this attempt.")
    sample_index: int = Field(..., ge=1, le=3, description="One-based sample being attempted.")
    attempt: int = Field(..., ge=1, description="One-based attempt for this scenario sample.")
    reserved_usd: Decimal = Field(..., gt=0, description="Worst-case pre-call reservation.")
    cost_usd: Decimal | None = Field(
        default=None,
        ge=0,
        description="Observed provider cost, or null while the reservation remains unsettled.",
    )


class JudgeCheckpoint(BaseModel):
    """Resumable partial judge state with conservative authorization accounting."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol hash.")
    model_id: str = Field(..., min_length=1, description="Judge catalog reference.")
    prompt_version: str = Field(..., min_length=1, description="Judge prompt version.")
    prompt_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Judge prompt hash.")
    samples: tuple[JudgeSample, ...] = Field(
        default=(), max_length=96, description="Completed, validated blind samples."
    )
    reservations: tuple[JudgeCallReservation, ...] = Field(
        default=(), description="All provider attempts, including unsettled interrupted calls."
    )

    @model_validator(mode="after")
    def _unique_progress(self) -> JudgeCheckpoint:
        sample_keys = {(item.scenario_id, item.sample_index) for item in self.samples}
        reservation_keys = {
            (item.scenario_id, item.sample_index, item.attempt) for item in self.reservations
        }
        if len(sample_keys) != len(self.samples):
            raise ValueError("judge checkpoint samples must be unique")
        if len(reservation_keys) != len(self.reservations):
            raise ValueError("judge checkpoint reservations must be unique")
        attempted_samples = {(item.scenario_id, item.sample_index) for item in self.reservations}
        if not sample_keys.issubset(attempted_samples):
            raise ValueError("completed judge samples require a provider reservation")
        return self

    @property
    def authorization_used_usd(self) -> Decimal:
        """Return settled spend plus full reservations for calls with unknown cost."""
        return sum(
            (
                item.cost_usd if item.cost_usd is not None else item.reserved_usd
                for item in self.reservations
            ),
            start=Decimal("0"),
        )

    @property
    def observed_spend_usd(self) -> Decimal:
        """Return provider costs observed for every completed response, valid or invalid."""
        return sum(
            (item.cost_usd for item in self.reservations if item.cost_usd is not None),
            start=Decimal("0"),
        )


class JudgmentArtifact(BaseModel):
    """All blind judge samples plus prompt/model provenance and bounded spend."""

    model_config = _MODEL_CONFIG

    run_id: str = Field(..., min_length=1, description="Evaluation run id.")
    config_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Protocol hash.")
    model_id: str = Field(..., min_length=1, description="Judge catalog reference.")
    model_family: str = Field(..., min_length=1, description="Judge model family.")
    prompt_version: str = Field(..., min_length=1, description="Judge prompt version.")
    prompt_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$", description="Judge prompt hash.")
    authorized_max_usd: Decimal = Field(..., gt=0, description="Explicit judge spend cap.")
    spent_usd: Decimal = Field(..., ge=0, description="Observed judge spend.")
    samples: tuple[JudgeSample, ...] = Field(
        ..., min_length=96, max_length=96, description="Three samples for all 32 scenarios."
    )

    @model_validator(mode="after")
    def _complete_and_bounded(self) -> JudgmentArtifact:
        if self.model_family != shared_model_family(self.model_id):
            raise ValueError("judge modelFamily must match the modelId family segment")
        keys = {(item.scenario_id, item.sample_index) for item in self.samples}
        scenario_ids = {item.scenario_id for item in self.samples}
        expected = {(scenario_id, sample) for scenario_id in scenario_ids for sample in (1, 2, 3)}
        if (
            len(scenario_ids) != _SCENARIO_COUNT
            or keys != expected
            or len(keys) != len(self.samples)
        ):
            raise ValueError("judgments must contain three samples for each of 32 scenarios")
        if self.spent_usd > self.authorized_max_usd:
            raise ValueError("judge spend exceeds its authorized hard cap")
        return self


class JudgeClient(Protocol):
    """The guarded generation method used by the judge stage."""

    async def generate(  # noqa: PLR0913 -- mirrors the shared client boundary.
        self,
        messages: Sequence[LlmMessage | dict[str, object]],
        *,
        model: str | None = None,
        overrides: GenerationParams | None = None,
        task_type: TaskType = TaskType.GENERATION,
        data_class: DataClass | None = None,
        include_raw: bool = False,
        capture_undeclared_tool_calls: bool = False,
        fallbacks: Sequence[str] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        tool_choice: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Generate one structured judge result."""
        ...
