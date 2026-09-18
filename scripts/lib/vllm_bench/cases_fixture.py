"""Summary: Deterministic SAR-evaluation fixture cases rendered through the production prompt.

Key classes:
- (none)

Key functions:
- build_benchmark_case: project one production SarInput into the exact benchmark request.
- build_fixture_cases: generate a provider-free smoke corpus from the 8-by-4 SAR matrix.

Notes:
- The fixture uses the committed scoring model and regulation corpus; it performs no network IO.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from fraudlens_backend.sar.egress import load_egress_policy, project_for_model
from fraudlens_backend.sar.evidence import build_evidence_catalog
from fraudlens_backend.sar.prompt import SarPromptTemplate, build_messages
from fraudlens_core import DEFAULT_RULE_DEFINITIONS, RiskPolicy, RuleRegistry
from fraudlens_llm import get_llm_settings
from fraudlens_llm.security.phi import mask_texts
from fraudlens_llm.security.policy import system_policy_message
from fraudlens_ml.pipeline import PipelineInput, RagResult, ScoreResult, ShapResult
from fraudlens_ml.pipeline.steps import build_rag_query, build_sar_input
from fraudlens_ml.rag import chunk_corpus, extract_citations, load_corpus
from fraudlens_ml.rag.retriever import RetrievedChunk
from fraudlens_ml.sar import SarCitation, SarFeature, SarInput
from fraudlens_ml.scoring import DeploymentPointer, Explainer, ModelCache, Scorer
from lib.sar_eval.config import SarEvalConfig, load_sar_eval_config
from lib.sar_eval.scenarios import (
    SarEvalScenario,
    generate_scenarios,
    scenario_rule_context,
)
from lib.study import sha256_hex
from lib.vllm_bench.config import VllmBenchConfig, resolve_profile
from lib.vllm_bench.state import (
    BenchmarkCase,
    BenchmarkMessage,
    CaseArtifact,
    CaseExclusion,
    CaseSet,
)

# The provider-free corpus must be reproducible from a clean checkout, so it scores through the
# committed fixture bundle rather than config/sar-eval.yaml's calibration model — that one pins a
# locally trained candidate which .gitignore (correctly) keeps out of the repository.
_FIXTURE_MODEL_VERSION = "v0-fixture"
_CHARS_PER_TOKEN = 4
_SHORT_HISTORY_LIMIT = 10
_SHORT_PROMPT_LIMIT = 4_000
_MEDIUM_PROMPT_LIMIT = 8_000


def _production_messages(sar_input: SarInput) -> tuple[BenchmarkMessage, ...]:
    """Apply the exact egress projection, prompt, policy prepend, and LLM masking stages."""
    model_input = project_for_model(sar_input, load_egress_policy())
    prompt = SarPromptTemplate.load()
    raw_messages = [
        system_policy_message().model_dump(mode="json"),
        *build_messages(prompt, model_input),
    ]
    settings = get_llm_settings()
    contents = [str(message.get("content") or "") for message in raw_messages]
    masked, _report = mask_texts(contents, settings.phi_masking_mode)
    return tuple(
        BenchmarkMessage.model_validate({"role": message["role"], "content": content})
        for message, content in zip(raw_messages, masked, strict=True)
    )


def _band(value: Decimal, boundaries: tuple[Decimal, ...], labels: tuple[str, ...]) -> str:
    """Place a numeric value into one configured stable categorical band."""
    for boundary, label in zip(boundaries, labels, strict=True):
        if value < boundary:
            return label
    return labels[-1]


def build_benchmark_case(  # noqa: PLR0913 - explicit case metadata is persisted verbatim.
    *,
    case_id: str,
    case_set: CaseSet,
    source_dataset: str,
    sar_input: SarInput,
    required_facts: tuple[str, ...],
    expected_citation_ids: tuple[str, ...],
    history_length: int,
    payment_format: str,
) -> BenchmarkCase:
    """Render one production SarInput and attach closed deterministic quality expectations."""
    messages = _production_messages(sar_input)
    prompt_chars = sum(len(message.content) for message in messages)
    offered = tuple(citation.citation for citation in sar_input.citations)
    model_input = project_for_model(sar_input, load_egress_policy())
    evidence_refs = tuple(fact.ref for fact in build_evidence_catalog(model_input).facts)
    return BenchmarkCase(
        case_id=case_id,
        case_set=case_set,
        source_dataset=source_dataset,
        data_class="synthetic",
        source="ibm-aml-synthetic",
        messages=messages,
        required_facts=required_facts,
        offered_citation_ids=offered,
        expected_citation_ids=expected_citation_ids,
        available_evidence_refs=() if case_set == "abstention" else evidence_refs,
        payment_format=payment_format,
        sar_input=sar_input,
        amount_band=_band(
            sar_input.amount,
            (Decimal("1000"), Decimal("10000"), Decimal("100000"), Decimal("Infinity")),
            ("under-1k", "1k-10k", "10k-100k", "100k-plus"),
        ),
        history_length_band=(
            "none"
            if history_length == 0
            else "short"
            if history_length < _SHORT_HISTORY_LIMIT
            else "long"
        ),
        prompt_length_band=(
            "short"
            if prompt_chars < _SHORT_PROMPT_LIMIT
            else "medium"
            if prompt_chars < _MEDIUM_PROMPT_LIMIT
            else "long"
        ),
        prompt_chars=prompt_chars,
    )


def _rag_result(expected_ids: tuple[str, ...]) -> RagResult:
    """Build deterministic citation evidence from the same committed corpus used by egress."""
    policy = load_egress_policy()
    chunks = chunk_corpus(
        load_corpus(policy.corpus_root),
        chunk_size=policy.regulation_corpus.chunk_size,
        overlap=policy.regulation_corpus.chunk_overlap,
    )
    selected: list[RetrievedChunk] = []
    for citation_id in expected_ids:
        match = next((chunk for chunk in chunks if chunk.citation == citation_id), None)
        if match is not None:
            selected.append(
                RetrievedChunk(
                    chunk_id=match.chunk_id,
                    doc_id=match.doc_id,
                    citation=match.citation,
                    title=match.title,
                    source=match.source,
                    text=match.text,
                    score=1.0,
                )
            )
    citations = tuple(
        SarCitation(
            citation=item.citation,
            title=item.title,
            source=item.source,
            snippet=item.snippet,
        )
        for item in extract_citations(selected, max_chars=policy.regulation_corpus.snippet_chars)
    )
    return RagResult(
        citations=citations,
        mode="lexical",
        rag_version="rag-v1",
    )


def _fixture_input(  # noqa: PLR0913 - injected production collaborators avoid repeated loading.
    scenario: SarEvalScenario,
    fixture_config: SarEvalConfig,
    *,
    cache: ModelCache,
    scorer: Scorer,
    explainer: Explainer,
    abstention: bool,
) -> tuple[SarInput, int]:
    """Run production rules, score, SHAP, risk, RAG, and SarInput builders for one fixture."""
    context = scenario_rule_context(scenario, fixture_config)
    evaluation = RuleRegistry().evaluate(DEFAULT_RULE_DEFINITIONS, context)
    version = _FIXTURE_MODEL_VERSION
    pointer = DeploymentPointer(active_version_label=version, active_artifact_uri=version)
    score_output = scorer.score(pointer, context)
    score = ScoreResult(
        fraud_probability=score_output.fraud_probability,
        model_version_label=score_output.model_version_label,
        risk_thresholds=score_output.risk_thresholds,
    )
    explanation = explainer.explain(cache.get(pointer), context)
    shap = ShapResult(
        base_value=explanation.base_value,
        shap_values=explanation.shap_values,
        top_features=tuple(
            SarFeature(feature=item.feature, value=item.value, shap_value=item.shap_value)
            for item in explanation.top_features
        ),
    )
    assessment = RiskPolicy().assess(
        fraud_probability=score.fraud_probability,
        rules_subscore=evaluation.subscore,
        model_thresholds=score.risk_thresholds,
    )
    subject = scenario.transactions[-1]
    pipeline_input = PipelineInput(
        agency_id="benchmark-synthetic",
        run_id=f"fixture-{scenario.scenario_id}",
        transaction_id=hashlib.sha256(subject.external_id.encode()).hexdigest()[:20],
        source="ibm-aml-synthetic",
        rule_context=context,
        amount=subject.amount,
        currency=subject.currency,
        country=subject.country,
        channel=subject.channel,
        feature_hash=hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
    )
    expected = scenario.expected_citation_ids
    rag = _rag_result(()) if abstention else _rag_result(expected)
    query = build_rag_query(evaluation, pipeline_input)
    if query == "":
        raise RuntimeError("fixture RAG query must be non-empty")
    sar_input = build_sar_input(
        pipeline_input=pipeline_input,
        evaluation=evaluation,
        score=score,
        shap=shap,
        rag=rag,
        assessment=assessment,
    )
    return sar_input, len(context.history)


def build_fixture_cases(
    config: VllmBenchConfig,
    *,
    profile: str,
    repo_root: Path,
) -> CaseArtifact:
    """Generate a hash-bound provider-free benchmark corpus from the SAR scenario matrix."""
    fixture_path = repo_root / config.cases.fixture_source
    fixture_bytes = fixture_path.read_bytes()
    fixture_config = load_sar_eval_config(fixture_path)
    scenarios = generate_scenarios(fixture_config, fixture_bytes).scenarios
    requested, _levels, warmup_count = resolve_profile(config, profile)
    measured_count = min(requested, len(scenarios))
    measured: list[BenchmarkCase] = []
    exclusions: list[CaseExclusion] = []
    prompt = SarPromptTemplate.load()
    model_root = repo_root / "data" / "models"
    cache = ModelCache(model_root)
    scorer = Scorer(cache)
    explainer = Explainer(top_k=8)
    max_prompt_chars = (config.server.max_model_len - config.request.max_tokens) * _CHARS_PER_TOKEN
    for scenario in scenarios:
        sar_input, history_length = _fixture_input(
            scenario,
            fixture_config,
            cache=cache,
            scorer=scorer,
            explainer=explainer,
            abstention=False,
        )
        required = (
            str(sar_input.amount),
            sar_input.currency,
            sar_input.occurred_at.date().isoformat(),
            scenario.typology.value.replace("_", " "),
        )
        case = build_benchmark_case(
            case_id=f"fixture-{scenario.scenario_id}",
            case_set="measured",
            source_dataset="sar-eval",
            sar_input=sar_input,
            required_facts=required,
            expected_citation_ids=tuple(
                item
                for item in scenario.expected_citation_ids
                if item in {citation.citation for citation in sar_input.citations}
            ),
            history_length=history_length,
            payment_format=scenario.transactions[-1].channel,
        )
        if case.prompt_chars > max_prompt_chars:
            exclusions.append(
                CaseExclusion(
                    source_dataset="sar-eval",
                    reason="context_limit",
                    prompt_chars=case.prompt_chars,
                )
            )
            continue
        measured.append(case)
        if len(measured) == measured_count:
            break
    if len(measured) != measured_count:
        raise ValueError(
            f"fixture case quota deficient: required {measured_count}, built {len(measured)}"
        )
    development = tuple(
        case.model_copy(update={"case_id": f"dev-{index:02d}", "case_set": "development"})
        for index, case in enumerate(measured[: min(4, len(measured))])
    )
    warmup = tuple(
        measured[index % len(measured)].model_copy(
            update={"case_id": f"warmup-{index:02d}", "case_set": "warmup"}
        )
        for index in range(warmup_count)
    )
    abstention: list[BenchmarkCase] = []
    for index, scenario in enumerate(scenarios[: min(2, len(scenarios))]):
        sar_input, history_length = _fixture_input(
            scenario,
            fixture_config,
            cache=cache,
            scorer=scorer,
            explainer=explainer,
            abstention=True,
        )
        abstention.append(
            build_benchmark_case(
                case_id=f"abstention-{index:02d}",
                case_set="abstention",
                source_dataset="sar-eval",
                sar_input=sar_input,
                required_facts=(str(sar_input.amount), sar_input.currency),
                expected_citation_ids=(),
                history_length=history_length,
                payment_format=scenario.transactions[-1].channel,
            )
        )
    return CaseArtifact(
        protocol_version=config.protocol_version,
        profile=profile,
        case_source="sar-eval",
        config_sha256=config.config_sha256,
        upstream_sha256=sha256_hex(fixture_bytes),
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.prompt_hash,
        distinct_subjects=len(measured),
        subject_overlap=0,
        cases=(*measured, *development, *warmup, *abstention),
        exclusions=tuple(exclusions),
    )
