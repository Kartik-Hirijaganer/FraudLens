"""Summary: Blind evidence assembly, pricing, unblinding, and quote-integrity checks.

Key classes:
- (none)

Key functions:
- pair: resolve the two workflow arms for one scenario.
- tool_evidence_for:
- messages: assemble blind judge input from shared facts and narratives.
- price:
- reserve:
- unblind:
- canonicalize_quote_integrity: bind returned quotes to exact candidate spans.

Notes:
- Workflow identity is absent from provider messages and restored only after validation.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from fraudlens_backend.sar.budget import estimate_cost_usd
from fraudlens_llm import Catalog, LlmMessage, LlmUsage, Role
from lib.sar_eval.config import SarEvalConfig
from lib.sar_eval.judge_contracts import (
    ArmJudgeSample,
    CandidateLabel,
    CandidateScore,
    ElementScore,
    JudgePromptTemplate,
    JudgeResponse,
)
from lib.sar_eval.runner import (
    ApiArmResult,
    ApiRunArtifact,
    Arm,
    DurableEvaluationFacts,
    ToolEvidenceFact,
)
from lib.sar_eval.scenarios import SarEvalScenario

_ELEMENTS = ("who", "what", "when", "where", "why")
_MODEL_REFERENCE_PARTS = 3
_MIN_QUOTE_ANCHOR_TOKENS = 4
_QUOTE_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)


def pair(results: ApiRunArtifact, scenario_id: str) -> dict[Arm, ApiArmResult]:
    paired = {item.arm: item for item in results.results if item.scenario_id == scenario_id}
    if set(paired) != {"single_writer", "multi_agent"}:
        raise ValueError("scenario is missing a paired API arm")
    return paired


def tool_evidence_for(results: ApiRunArtifact, scenario_id: str) -> tuple[ToolEvidenceFact, ...]:
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


def messages(  # noqa: PLR0913, PLR0917 -- explicit blind-evidence inputs prevent candidate leakage.
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


def price(catalog: Catalog, model_ref: str, usage: LlmUsage) -> Decimal:
    _provider, _model_id, card = catalog.get(model_ref)
    return estimate_cost_usd(card, usage)


def reserve(catalog: Catalog, config: SarEvalConfig) -> Decimal:
    return price(
        catalog,
        config.judge.model,
        LlmUsage(
            input_tokens=config.judge.max_input_bytes,
            output_tokens=config.judge.max_output_tokens,
            total_tokens=config.judge.max_input_bytes + config.judge.max_output_tokens,
        ),
    )


def unblind(
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


def _canonical_span(span: str, narrative: str) -> str | None:
    """Map quote drift to one unique exact narrative substring with a strong token anchor."""
    if span in narrative:
        return span
    stripped = span.strip()
    if stripped in narrative:
        return stripped
    requested_tokens = stripped.split()
    if not requested_tokens:
        return None
    narrative_tokens = list(re.finditer(r"\S+", narrative))

    def matches(tokens: list[str]) -> list[str]:
        width = len(tokens)
        normalized_tokens = [item.translate(_QUOTE_PUNCTUATION_TRANSLATION) for item in tokens]
        found: list[str] = []
        for index in range(len(narrative_tokens) - width + 1):
            candidate_tokens = [
                item.group(0).translate(_QUOTE_PUNCTUATION_TRANSLATION)
                for item in narrative_tokens[index : index + width]
            ]
            if candidate_tokens == normalized_tokens:
                found.append(
                    narrative[
                        narrative_tokens[index].start() : narrative_tokens[index + width - 1].end()
                    ]
                )
        return found

    exact_matches = matches(requested_tokens)
    if exact_matches:
        return exact_matches[0] if len(exact_matches) == 1 else None
    minimum_width = max(_MIN_QUOTE_ANCHOR_TOKENS, (len(requested_tokens) + 1) // 2)
    for width in range(len(requested_tokens) - 1, minimum_width - 1, -1):
        anchored: list[str] = []
        for start in range(len(requested_tokens) - width + 1):
            anchored.extend(matches(requested_tokens[start : start + width]))
        if anchored:
            return anchored[0] if len(anchored) == 1 else None
    return None


def canonicalize_quote_integrity(
    response: JudgeResponse,
    narratives: dict[CandidateLabel, str],
) -> JudgeResponse:
    """Canonicalize uniquely anchored drift and reject every unresolved judge quote."""
    candidates: list[CandidateScore] = []
    for candidate in response.candidates:
        narrative = narratives[candidate.candidate]
        claims = tuple(
            claim.model_copy(update={"quoted_span": _canonical_span(claim.quoted_span, narrative)})
            for claim in candidate.unsupported_claims
        )
        elements: list[ElementScore] = []
        for element in candidate.elements:
            canonical = (
                _canonical_span(element.quoted_span, narrative)
                if element.quoted_span is not None
                else None
            )
            elements.append(
                element.model_copy(
                    update={
                        "present": element.present and canonical is not None,
                        "quoted_span": canonical,
                    }
                )
            )
        canonical_elements = tuple(elements)
        spans = [claim.quoted_span for claim in claims]
        spans.extend(
            element.quoted_span for element in canonical_elements if element.quoted_span is not None
        )
        if any(span is None or span not in narrative for span in spans):
            raise RuntimeError("judge returned a quoted span absent from its candidate narrative")
        candidates.append(
            candidate.model_copy(
                update={"unsupported_claims": claims, "elements": canonical_elements}
            )
        )
    return response.model_copy(update={"candidates": tuple(candidates)})
