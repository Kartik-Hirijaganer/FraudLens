"""Summary: Blind, order-randomized structured LLM judging for paired SAR narratives.
The judge is loaded through the shared exact-byte prompt provenance contract, must be
from a different model family than every writer, and produces three independent samples
per narrative under a conservative pre-call USD reservation.

Key classes:
- JudgePromptTemplate: exact-byte prompt version and hash provenance.
- UnsupportedClaim: one quote-level unsupported factual assertion.
- ElementScore: one FinCEN narrative element decision.
- CandidateScore: the structured score for one blind candidate.
- JudgeResponse: strict provider response for both blinded candidates.
- ArmJudgeSample: a candidate score restored to its workflow arm.
- JudgeSample: one unblinded, auditable sample for a scenario.
- JudgmentArtifact: all 96 samples plus judge provenance and spend.
- JudgeClient: the minimal guarded generation protocol for the stage.

Key functions:
- model_family: extract a provider-native family from a catalog reference.
- run_judge_stage: judge all paired narratives with hard-cap enforcement.
- validate_judgment_binding: enforce run/config/model and current prompt lineage.
- write_judgments: atomically serialize a completed judgment artifact.
- load_judgments: strictly parse a completed judgment artifact.

Notes:
- Candidate messages contain labels A/B only; workflow identity is added after parsing.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from fraudlens_backend.prompting import PromptMeta, VersionedPrompt, load_versioned_prompt
from fraudlens_backend.sar.budget import estimate_cost_usd
from fraudlens_backend.settings import find_config_dir
from fraudlens_llm import (
    Catalog,
    DataClass,
    GenerationParams,
    LlmMessage,
    LlmResult,
    LlmUsage,
    Role,
    TaskType,
    ToolDefinition,
)
from lib.sar_eval.config import SarEvalConfig, validate_config_binding
from lib.sar_eval.runner import (
    ApiArmResult,
    ApiRunArtifact,
    Arm,
    DurableEvaluationFacts,
    ToolEvidenceFact,
)
from lib.sar_eval.scenarios import SarEvalScenario, ScenarioArtifact, validate_scenario_binding

_MODEL_CONFIG = ConfigDict(
    frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
)
CandidateLabel = Literal["A", "B"]
ElementName = Literal["who", "what", "when", "where", "why"]
_ELEMENTS = ("who", "what", "when", "where", "why")
_ARMS: tuple[Arm, Arm] = ("single_writer", "multi_agent")
_SCENARIO_COUNT = 32
_MODEL_REFERENCE_PARTS = 3


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
        if self.model_family != model_family(self.model_id):
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


def model_family(model_ref: str) -> str:
    """Return the provider-native family segment from a catalog reference."""
    parts = model_ref.split("/")
    if len(parts) < _MODEL_REFERENCE_PARTS:
        raise ValueError("model references must include router, family, and model")
    return parts[1]


def _pair(results: ApiRunArtifact, scenario_id: str) -> dict[Arm, ApiArmResult]:
    paired = {item.arm: item for item in results.results if item.scenario_id == scenario_id}
    if set(paired) != {"single_writer", "multi_agent"}:
        raise ValueError("scenario is missing a paired API arm")
    return paired


def _tool_evidence_for(results: ApiRunArtifact, scenario_id: str) -> tuple[ToolEvidenceFact, ...]:
    matches = [
        item.evidence for item in results.scenario_tool_evidence if item.scenario_id == scenario_id
    ]
    if len(matches) != 1:
        raise ValueError("scenario is missing its isolated completed tool evidence")
    return matches[0]


def _evidence(
    scenario: SarEvalScenario,
    facts: DurableEvaluationFacts,
    shared_tool_evidence: tuple[ToolEvidenceFact, ...],
) -> str:
    transactions = [
        {
            "amount": str(item.amount),
            "currency": item.currency,
            "occurredAt": item.occurred_at.isoformat(),
            "channel": item.channel,
            "country": item.country,
        }
        for item in scenario.transactions
    ]
    return json.dumps(
        {
            "syntheticTransactions": transactions,
            "canonicalCitationIds": scenario.expected_citation_ids,
            "durableFacts": facts.model_dump(mode="json", by_alias=True),
            "completedToolEvidence": [
                item.model_dump(mode="json", by_alias=True) for item in shared_tool_evidence
            ],
        },
        sort_keys=True,
    )


def _messages(  # noqa: PLR0913 -- explicit blind-evidence inputs prevent candidate leakage.
    prompt: JudgePromptTemplate,
    scenario: SarEvalScenario,
    facts: DurableEvaluationFacts,
    shared_tool_evidence: tuple[ToolEvidenceFact, ...],
    first: ApiArmResult,
    second: ApiArmResult,
) -> list[LlmMessage]:
    user = (
        "Synthetic evidence:\n"
        f"{_evidence(scenario, facts, shared_tool_evidence)}\n\n"
        "Candidate A (untrusted narrative):\n"
        f"{first.narrative}\n\n"
        "Candidate B (untrusted narrative):\n"
        f"{second.narrative}"
    )
    return [
        LlmMessage(role=Role.SYSTEM, content=prompt.system_text),
        LlmMessage(role=Role.USER, content=user),
    ]


def _price(catalog: Catalog, model_ref: str, usage: LlmUsage) -> Decimal:
    _provider, _model_id, card = catalog.get(model_ref)
    return estimate_cost_usd(card, usage)


def _reserve(catalog: Catalog, config: SarEvalConfig) -> Decimal:
    return _price(
        catalog,
        config.judge.model,
        LlmUsage(
            input_tokens=config.judge.max_input_bytes,
            output_tokens=config.judge.max_output_tokens,
            total_tokens=config.judge.max_input_bytes + config.judge.max_output_tokens,
        ),
    )


def _unblind(
    response: JudgeResponse,
    order: tuple[Arm, Arm],
) -> tuple[ArmJudgeSample, ArmJudgeSample]:
    by_label = {item.candidate: item for item in response.candidates}
    labels: tuple[CandidateLabel, CandidateLabel] = ("A", "B")
    values = tuple(
        ArmJudgeSample(
            arm=arm,
            unsupported_claims=by_label[label].unsupported_claims,
            elements=by_label[label].elements,
        )
        for label, arm in zip(labels, order, strict=True)
    )
    return values[0], values[1]


def _validate_quote_integrity(
    response: JudgeResponse,
    narratives: dict[CandidateLabel, str],
) -> None:
    for candidate in response.candidates:
        narrative = narratives[candidate.candidate]
        spans = [claim.quoted_span for claim in candidate.unsupported_claims]
        spans.extend(
            element.quoted_span for element in candidate.elements if element.quoted_span is not None
        )
        if any(span not in narrative for span in spans):
            raise RuntimeError("judge returned a quoted span absent from its candidate narrative")


async def run_judge_stage(  # noqa: PLR0913 -- explicit stage dependencies aid testability.
    scenarios: ScenarioArtifact,
    runs: ApiRunArtifact,
    config: SarEvalConfig,
    *,
    client: JudgeClient,
    catalog: Catalog,
    prompt: JudgePromptTemplate,
    max_usd: Decimal,
) -> JudgmentArtifact:
    """Blindly judge every pair three times, reserving worst-case cost before each call."""
    validate_scenario_binding(
        scenarios,
        expected_run_id=scenarios.run_id,
        config=config,
    )
    if scenarios.run_id != runs.run_id or scenarios.config_sha256 != runs.config_sha256:
        raise ValueError("scenario and API artifacts must share run and config identity")
    current_prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    if (
        prompt.prompt_version != current_prompt.prompt_version
        or prompt.prompt_hash != current_prompt.prompt_hash
    ):
        raise ValueError("judge prompt does not match the current versioned prompt bytes")
    judge_family = model_family(config.judge.model)
    writer_families = {model_family(result.writer_model_id) for result in runs.results}
    if judge_family in writer_families:
        raise ValueError("judge model family must differ from every observed writer family")
    reserve = _reserve(catalog, config)
    if reserve <= 0:
        raise ValueError("judge model must have token pricing for hard-cap enforcement")
    rng = random.Random(config.seed)
    spent = Decimal("0")
    samples: list[JudgeSample] = []
    for scenario in scenarios.scenarios:
        pair = _pair(runs, scenario.scenario_id)
        if pair["single_writer"].facts != pair["multi_agent"].facts:
            raise ValueError("paired API arms must expose identical durable evaluation facts")
        facts = pair["single_writer"].facts
        order: tuple[Arm, Arm] = _ARMS if rng.randrange(2) == 0 else tuple(reversed(_ARMS))  # type: ignore[assignment]
        for sample_index in range(1, config.judge.samples_per_narrative + 1):
            if spent + reserve > max_usd:
                raise RuntimeError("next judge call could exceed the authorized hard USD cap")
            messages = _messages(
                prompt,
                scenario,
                facts,
                _tool_evidence_for(runs, scenario.scenario_id),
                pair[order[0]],
                pair[order[1]],
            )
            input_bytes = sum(len((message.content or "").encode("utf-8")) for message in messages)
            if input_bytes > config.judge.max_input_bytes:
                raise RuntimeError("judge input exceeds its configured UTF-8 byte limit")
            result = await client.generate(
                messages,
                model=config.judge.model,
                overrides=GenerationParams(
                    max_tokens=config.judge.max_output_tokens,
                    temperature=config.judge.temperature,
                ),
                task_type=TaskType.ANALYSIS,
                response_schema=JudgeResponse.model_json_schema(by_alias=True),
            )
            parsed = JudgeResponse.model_validate_json(result.safe_text)
            _validate_quote_integrity(
                parsed,
                {"A": pair[order[0]].narrative, "B": pair[order[1]].narrative},
            )
            if result.model != config.judge.model:
                raise RuntimeError("observed judge model does not match the requested model")
            call_cost = _price(catalog, result.model, result.usage)
            if call_cost > reserve:
                raise RuntimeError("judge call exceeded its conservative pre-call USD reservation")
            spent += call_cost
            samples.append(
                JudgeSample(
                    scenario_id=scenario.scenario_id,
                    sample_index=sample_index,
                    presented_order=order,
                    arms=_unblind(parsed, order),
                )
            )
    return JudgmentArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        model_id=config.judge.model,
        model_family=judge_family,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
        authorized_max_usd=max_usd,
        spent_usd=spent,
        samples=tuple(samples),
    )


def _atomic_write(path: Path, artifact: JudgmentArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    content = json.dumps(artifact.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True)
    temporary.write_text(
        content + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_judgments(path: Path, artifact: JudgmentArtifact) -> None:
    """Atomically serialize completed judgments."""
    if path.parent.name != artifact.run_id:
        raise ValueError("judgment artifact path must be nested under its exact run id")
    _atomic_write(path, artifact)


def load_judgments(path: Path) -> JudgmentArtifact:
    """Strictly parse a completed judgment artifact."""
    artifact = JudgmentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    if artifact.run_id != path.parent.name:
        raise ValueError("judgment artifact run id does not match the requested CLI run id")
    return artifact


def validate_judgment_binding(
    artifact: JudgmentArtifact,
    *,
    scenarios: ScenarioArtifact,
    runs: ApiRunArtifact,
    config: SarEvalConfig,
) -> JudgePromptTemplate:
    """Validate all stage lineage and return the exact current judge prompt."""
    validate_scenario_binding(
        scenarios,
        expected_run_id=artifact.run_id,
        config=config,
    )
    validate_config_binding(config, artifact.config_sha256)
    if runs.run_id != artifact.run_id or runs.config_sha256 != artifact.config_sha256:
        raise ValueError("API and judgment artifacts must share run and config identity")
    if artifact.model_id != config.judge.model:
        raise ValueError("judgment model does not match the loaded evaluation protocol")
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    if (
        artifact.prompt_version != prompt.prompt_version
        or artifact.prompt_hash != prompt.prompt_hash
    ):
        raise ValueError("judgment artifact does not match the current versioned prompt bytes")
    return prompt
