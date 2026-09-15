"""Summary: Stable orchestration facade for blind, randomized SAR LLM judging.

Key classes:
- (none)

Key functions:
- run_judge_stage: judge all paired narratives within the authorized hard cap.
- write_judgments: atomically serialize a completed judgment artifact.
- load_judgments:
- validate_judgment_binding: enforce artifact and prompt lineage.

Notes:
- The quote-canonicalization test seam remains explicitly re-exported.
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path

from fraudlens_llm import Catalog, GenerationParams, TaskType
from lib.sar_eval.config import SarEvalConfig, validate_config_binding
from lib.sar_eval.judge_contracts import (
    ArmJudgeSample,
    CandidateLabel,
    CandidateScore,
    ElementName,
    ElementScore,
    JudgeCallReservation,
    JudgeCheckpoint,
    JudgeClient,
    JudgePromptTemplate,
    JudgeResponse,
    JudgeSample,
    JudgmentArtifact,
    UnsupportedClaim,
)
from lib.sar_eval.judge_grounding import (
    canonicalize_quote_integrity,
    price,
    tool_evidence_for,
    unblind,
)
from lib.sar_eval.judge_grounding import (
    messages as build_messages,
)
from lib.sar_eval.judge_grounding import (
    pair as paired_results,
)
from lib.sar_eval.judge_grounding import (
    reserve as reserve_cost,
)
from lib.sar_eval.runner import ApiRunArtifact, Arm, _paired_facts_equal
from lib.sar_eval.scenarios import ScenarioArtifact, validate_scenario_binding
from lib.study.artifacts import atomic_write_model
from lib.study.binding import model_family

_ARMS: tuple[Arm, Arm] = ("single_writer", "multi_agent")

_canonicalize_quote_integrity = canonicalize_quote_integrity

__all__ = [
    "ArmJudgeSample",
    "CandidateLabel",
    "CandidateScore",
    "ElementName",
    "ElementScore",
    "JudgeCallReservation",
    "JudgeCheckpoint",
    "JudgeClient",
    "JudgePromptTemplate",
    "JudgeResponse",
    "JudgeSample",
    "JudgmentArtifact",
    "UnsupportedClaim",
    "_canonicalize_quote_integrity",
    "load_judgments",
    "model_family",
    "run_judge_stage",
    "validate_judgment_binding",
    "write_judgments",
]


def _new_checkpoint(
    scenarios: ScenarioArtifact,
    config: SarEvalConfig,
    prompt: JudgePromptTemplate,
) -> JudgeCheckpoint:
    """Create empty judge progress bound to the current immutable protocol inputs."""
    return JudgeCheckpoint(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        model_id=config.judge.model,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
    )


def _load_checkpoint(
    path: Path | None,
    scenarios: ScenarioArtifact,
    config: SarEvalConfig,
    prompt: JudgePromptTemplate,
) -> JudgeCheckpoint:
    """Load resumable progress or initialize it, rejecting any protocol drift."""
    expected = _new_checkpoint(scenarios, config, prompt)
    if path is None or not path.exists():
        return expected
    checkpoint = JudgeCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    binding = (
        "run_id",
        "config_sha256",
        "model_id",
        "prompt_version",
        "prompt_hash",
    )
    if any(getattr(checkpoint, field) != getattr(expected, field) for field in binding):
        raise ValueError("judge checkpoint does not match the current run and protocol")
    return checkpoint


def _write_checkpoint(path: Path | None, checkpoint: JudgeCheckpoint) -> None:
    """Atomically persist partial progress when the CLI supplies a checkpoint path."""
    if path is not None:
        atomic_write_model(path, checkpoint)


def _settle_reservation(
    checkpoint: JudgeCheckpoint,
    reservation_index: int,
    cost_usd: Decimal,
) -> JudgeCheckpoint:
    """Replace one pending reservation with its observed provider cost."""
    reservations = list(checkpoint.reservations)
    reservations[reservation_index] = reservations[reservation_index].model_copy(
        update={"cost_usd": cost_usd}
    )
    return checkpoint.model_copy(update={"reservations": tuple(reservations)})


async def run_judge_stage(  # noqa: PLR0913 -- explicit stage dependencies aid testability.
    scenarios: ScenarioArtifact,
    runs: ApiRunArtifact,
    config: SarEvalConfig,
    *,
    client: JudgeClient,
    catalog: Catalog,
    prompt: JudgePromptTemplate,
    max_usd: Decimal,
    checkpoint_path: Path | None = None,
) -> JudgmentArtifact:
    """Blindly judge every pair three times with durable pre-call cost reservations."""
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
    reserved_per_call = reserve_cost(catalog, config)
    if reserved_per_call <= 0:
        raise ValueError("judge model must have token pricing for hard-cap enforcement")
    checkpoint = _load_checkpoint(checkpoint_path, scenarios, config, prompt)
    if checkpoint.authorization_used_usd > max_usd:
        raise RuntimeError("judge checkpoint already exceeds the authorized hard USD cap")
    rng = random.Random(config.seed)
    sample_map = {(item.scenario_id, item.sample_index): item for item in checkpoint.samples}
    ordered_keys: list[tuple[str, int]] = []
    for scenario in scenarios.scenarios:
        pair = paired_results(runs, scenario.scenario_id)
        if not _paired_facts_equal(
            pair["single_writer"].facts,
            pair["multi_agent"].facts,
        ):
            raise ValueError("paired API arms must expose identical durable evaluation facts")
        facts = pair["single_writer"].facts
        order: tuple[Arm, Arm] = _ARMS if rng.randrange(2) == 0 else tuple(reversed(_ARMS))  # type: ignore[assignment]
        for sample_index in range(1, config.judge.samples_per_narrative + 1):
            sample_key = (scenario.scenario_id, sample_index)
            ordered_keys.append(sample_key)
            if sample_key in sample_map:
                continue
            judge_messages = build_messages(
                prompt,
                scenario,
                facts,
                tool_evidence_for(runs, scenario.scenario_id),
                pair[order[0]],
                pair[order[1]],
            )
            input_bytes = sum(
                len((message.content or "").encode("utf-8")) for message in judge_messages
            )
            if input_bytes > config.judge.max_input_bytes:
                raise RuntimeError("judge input exceeds its configured UTF-8 byte limit")
            if checkpoint.authorization_used_usd + reserved_per_call > max_usd:
                raise RuntimeError("next judge call could exceed the authorized hard USD cap")
            attempt = (
                sum(
                    item.scenario_id == scenario.scenario_id and item.sample_index == sample_index
                    for item in checkpoint.reservations
                )
                + 1
            )
            reservation = JudgeCallReservation(
                scenario_id=scenario.scenario_id,
                sample_index=sample_index,
                attempt=attempt,
                reserved_usd=reserved_per_call,
            )
            checkpoint = checkpoint.model_copy(
                update={"reservations": (*checkpoint.reservations, reservation)}
            )
            reservation_index = len(checkpoint.reservations) - 1
            _write_checkpoint(checkpoint_path, checkpoint)
            result = await client.generate(
                judge_messages,
                model=config.judge.model,
                overrides=GenerationParams(
                    max_tokens=config.judge.max_output_tokens,
                    temperature=config.judge.temperature,
                ),
                task_type=TaskType.ANALYSIS,
                response_schema=JudgeResponse.model_json_schema(by_alias=True),
            )
            call_cost = price(catalog, result.model, result.usage)
            checkpoint = _settle_reservation(checkpoint, reservation_index, call_cost)
            _write_checkpoint(checkpoint_path, checkpoint)
            if call_cost > reserved_per_call:
                raise RuntimeError("judge call exceeded its conservative pre-call USD reservation")
            parsed = JudgeResponse.model_validate_json(result.safe_text)
            try:
                parsed = canonicalize_quote_integrity(
                    parsed,
                    {"A": pair[order[0]].narrative, "B": pair[order[1]].narrative},
                )
            except RuntimeError:
                if checkpoint_path is not None:
                    atomic_write_model(checkpoint_path.with_name("judgments.invalid.json"), parsed)
                raise
            if result.model != config.judge.model:
                raise RuntimeError("observed judge model does not match the requested model")
            sample = JudgeSample(
                scenario_id=scenario.scenario_id,
                sample_index=sample_index,
                presented_order=order,
                arms=unblind(parsed, order),
            )
            sample_map[sample_key] = sample
            checkpoint = checkpoint.model_copy(update={"samples": (*checkpoint.samples, sample)})
            _write_checkpoint(checkpoint_path, checkpoint)
    return JudgmentArtifact(
        run_id=scenarios.run_id,
        config_sha256=scenarios.config_sha256,
        model_id=config.judge.model,
        model_family=judge_family,
        prompt_version=prompt.prompt_version,
        prompt_hash=prompt.prompt_hash,
        authorized_max_usd=max_usd,
        spent_usd=checkpoint.observed_spend_usd,
        samples=tuple(sample_map[key] for key in ordered_keys),
    )


def write_judgments(path: Path, artifact: JudgmentArtifact) -> None:
    """Atomically serialize completed judgments."""
    if path.parent.name != artifact.run_id:
        raise ValueError("judgment artifact path must be nested under its exact run id")
    atomic_write_model(path, artifact)


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
