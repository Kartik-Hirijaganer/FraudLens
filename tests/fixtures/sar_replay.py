"""The committed replay corpus of REAL model output from the persisted vLLM benchmark run.

The citation suite used to draft through `MockSarDrafter`, whose `cited_regulations` IS the offered
set — precision and recall were 1.0 by construction and the gate was never actually asked anything.
This corpus replaces that with 40 real per-case outputs from run `vllm-bench-f810b57a7b8ae05a`,
both arms, including the cases that genuinely fabricated citation ids.

Selection is deterministic and stratified over the four joint (AWQ, BF16) gate outcomes at
concurrency 32, in the population's own proportions, so a 40-case corpus reproduces the full run's
arm pass rates to within one case: AWQ 0.725 vs 0.736, BF16 0.925 vs 0.938, cascade 0.950 vs 0.958.
The full-run rates are recorded in the fixture's `population` block so a test asserts against the
measured population rather than against a number retyped from the plan.

Each case carries only what a deterministic replay needs — offered/expected citation ids, available
evidence refs, required facts, and per arm the raw output, finish reason, and the verdict recorded
when the corpus was built. Prompts, telemetry, timings, and token accounting are dropped. The cases
are IBM-derived synthetic AML transactions whose model output is already masked; no identifiers,
no real-person data.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fraudlens_ml.rag.citations import escape_as_data
from fraudlens_ml.sar import SarCitation

REPLAY_CORPUS_PATH = Path(__file__).resolve().parent / "sar_replay_corpus.json"
# The committed pilot re-derives this corpus's population figures from the full persisted run, so
# the fixture's provenance block can be checked against code instead of trusted.
REPLAY_PILOT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "reference"
    / "benchmarks"
    / "vllm-cascade-replay-pilot.json"
)
AWQ_ARM = "awq"
BF16_ARM = "bf16"


class ReplayArmOutput(BaseModel):
    """One arm's real output for one case, with the verdict recorded when it was committed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(..., description="Raw model output exactly as the provider returned it.")
    finish_reason: str | None = Field(
        default=None, alias="finishReason", description="Provider finish reason for this output."
    )
    expected_passed: bool = Field(
        ..., alias="expectedPassed", description="Gate verdict recorded when the corpus was built."
    )
    expected_reasons: tuple[str, ...] = Field(
        default=(), alias="expectedReasons", description="Recorded rejection reason codes."
    )


class ReplayCase(BaseModel):
    """One benchmark case with the offered evidence both arms were given."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., alias="caseId", description="Stable synthetic benchmark case id.")
    offered_citation_ids: tuple[str, ...] = Field(
        ..., alias="offeredCitationIds", description="Citation ids the prompt actually offered."
    )
    expected_citation_ids: tuple[str, ...] = Field(
        ..., alias="expectedCitationIds", description="Ground-truth citation ids for recall."
    )
    available_evidence_refs: tuple[str, ...] = Field(
        ..., alias="availableEvidenceRefs", description="Trusted evidence refs for this case."
    )
    required_facts: tuple[str, ...] = Field(
        ..., alias="requiredFacts", description="Ground-truth facts a faithful draft restates."
    )
    arms: dict[str, ReplayArmOutput] = Field(..., description="Real output keyed by arm name.")

    def offered_citations(self) -> tuple[SarCitation, ...]:
        """Rebuild the offered citation objects the gate judges membership against."""
        return tuple(
            SarCitation(
                citation=citation_id,
                title=f"Provision {citation_id}",
                source="regulations",
                snippet=escape_as_data(f"Synthetic replay excerpt for {citation_id}."),
            )
            for citation_id in self.offered_citation_ids
        )


class ReplayPopulation(BaseModel):
    """The full-run rates the bounded corpus is a stratified sample of."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cases: int = Field(..., gt=0, description="Cases measured in the full persisted run.")
    awq_pass_rate: float = Field(
        ..., alias="awqPassRate", ge=0, le=1, description="AWQ-arm gate pass rate over the run."
    )
    bf16_pass_rate: float = Field(
        ..., alias="bf16PassRate", ge=0, le=1, description="BF16-arm gate pass rate over the run."
    )
    cascade_pass_rate: float = Field(
        ...,
        alias="cascadePassRate",
        ge=0,
        le=1,
        description="Share of cases at least one arm passed.",
    )
    awq_recall_mean: float = Field(
        ...,
        alias="awqRecallMean",
        ge=0,
        le=1,
        description="Mean expected-citation recall over AWQ drafts the gate accepted.",
    )
    bf16_recall_mean: float = Field(
        ...,
        alias="bf16RecallMean",
        ge=0,
        le=1,
        description="Mean expected-citation recall over BF16 drafts the gate accepted.",
    )

    def pass_rate(self, arm: str) -> float:
        """Return one arm's measured full-run gate pass rate."""
        return self.awq_pass_rate if arm == AWQ_ARM else self.bf16_pass_rate

    def recall_mean(self, arm: str) -> float:
        """Return one arm's measured full-run recall over accepted drafts."""
        return self.awq_recall_mean if arm == AWQ_ARM else self.bf16_recall_mean


class ReplaySample(BaseModel):
    """The bounded corpus's own recorded aggregates, locked against silent resampling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    awq_recall_mean: float = Field(
        ..., alias="awqRecallMean", ge=0, le=1, description="AWQ recall mean over this corpus."
    )
    bf16_recall_mean: float = Field(
        ..., alias="bf16RecallMean", ge=0, le=1, description="BF16 recall mean over this corpus."
    )

    def recall_mean(self, arm: str) -> float:
        """Return one arm's recorded recall mean over the sampled cases."""
        return self.awq_recall_mean if arm == AWQ_ARM else self.bf16_recall_mean


class ReplayCorpus(BaseModel):
    """The committed corpus document: provenance, population rates, and the sampled cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(..., alias="runId", description="Persisted benchmark run this came from.")
    concurrency: int = Field(..., gt=0, description="Concurrency level the outputs were served at.")
    policy_version: str = Field(
        ...,
        alias="policyVersion",
        description="Gate policy version the recorded verdicts were produced under.",
    )
    population: ReplayPopulation = Field(..., description="Full-run rates this corpus samples.")
    sample: ReplaySample = Field(..., description="Aggregates recomputable from the sampled cases.")
    cases: tuple[ReplayCase, ...] = Field(..., min_length=1, description="The sampled cases.")

    def arm(self, name: str) -> tuple[tuple[ReplayCase, ReplayArmOutput], ...]:
        """Return every case paired with one arm's real output."""
        return tuple((case, case.arms[name]) for case in self.cases)


@lru_cache(maxsize=1)
def load_replay_corpus() -> ReplayCorpus:
    """Load and strictly validate the committed replay corpus."""
    return ReplayCorpus.model_validate(json.loads(REPLAY_CORPUS_PATH.read_text(encoding="utf-8")))
