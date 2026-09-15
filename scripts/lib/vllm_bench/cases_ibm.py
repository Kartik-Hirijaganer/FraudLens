"""Summary: IBM final-test case selection through production scoring, risk, RAG, and prompt seams.

Key classes:
- (none)

Key functions:
- build_ibm_cases: build the exact measured/development/warm-up/abstention corpus or fail deficient.

Notes:
- Raw account keys remain transient in memory and never enter the case artifact or error text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fraudlens_backend.pipeline_ports import RetrieverAdapter
from fraudlens_backend.sar.prompt import SarPromptTemplate
from fraudlens_backend.settings import AppSettings
from fraudlens_core import (
    DEFAULT_RULE_DEFINITIONS,
    RiskPolicy,
    RuleContext,
    RuleRegistry,
    RuleTransaction,
    TransactionDirection,
)
from fraudlens_ml.pipeline import PipelineInput, ScoreResult, ShapResult
from fraudlens_ml.pipeline.steps import build_rag_query, build_sar_input
from fraudlens_ml.rag import HashingEmbedder, Retriever, index_status
from fraudlens_ml.sar import SarFeature, SarInput
from fraudlens_ml.scoring import DeploymentPointer, Explainer, ModelCache, Scorer
from lib.aml_mapping import ibm_channel, ibm_country, ibm_currency
from lib.fulldata.config import load_fulldata_config
from lib.fulldata.folds import folded_path
from lib.fulldata.ingest import ingested_scan
from lib.fulldata.train import load_candidate_evaluation
from lib.study import sha256_hex
from lib.vllm_bench.cases_fixture import build_benchmark_case
from lib.vllm_bench.config import VllmBenchConfig
from lib.vllm_bench.state import BenchmarkCase, CaseArtifact, CaseExclusion

_CHARS_PER_TOKEN = 4
_CANDIDATE_MULTIPLIER = 20


@dataclass(frozen=True)
class _Prepared:
    """Transient selected case plus raw subject key retained only for overlap accounting."""

    case: BenchmarkCase
    sar_input: SarInput
    subject_key: str


def _sql_text(value: str) -> str:
    """Quote a configuration-controlled SQL string."""
    return "'" + value.replace("'", "''") + "'"


def _candidate_rows(
    connection: duckdb.DuckDBPyConnection,
    *,
    fold_path: Path,
    source_scan: str,
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Read deterministic final-test candidates with raw facts needed to rebuild live context."""
    query = f"""
      SELECT f.row_id, i.occurred_at, i.origin_account, i.dest_account, i.amount,
        i.payment_currency, i.payment_format
      FROM read_parquet({_sql_text(str(fold_path))}) f
      INNER JOIN read_parquet({_sql_text(source_scan)}, hive_partitioning=false) i USING (row_id)
      WHERE f.fold = 'holdout'
      ORDER BY hash(f.row_id, ?), f.row_id
      LIMIT ?
    """
    cursor = connection.execute(query, [seed, limit])
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _relative_transaction(row: dict[str, Any], account: str) -> RuleTransaction:
    """Project one raw IBM row into an identifier-free account-relative transaction."""
    return RuleTransaction(
        amount=Decimal(str(row["amount"])),
        currency=ibm_currency(str(row["payment_currency"])),
        country=ibm_country(str(row["payment_currency"])),
        channel=ibm_channel(str(row["payment_format"])),
        occurred_at=row["occurred_at"],
        direction=(
            TransactionDirection.OUTBOUND
            if row["origin_account"] == account
            else TransactionDirection.INBOUND
        ),
    )


def _history(  # noqa: PLR0913 - exact bounded history query inputs remain explicit.
    connection: duckdb.DuckDBPyConnection,
    *,
    source_scan: str,
    account: str,
    before: datetime,
    hours: int,
    limit: int,
) -> tuple[RuleTransaction, ...]:
    """Load the exact bounded half-open history for one transient account key."""
    query = f"""
      SELECT amount, payment_currency, payment_format, occurred_at, origin_account, dest_account
      FROM read_parquet({_sql_text(source_scan)}, hive_partitioning=false)
      WHERE occurred_at >= ? AND occurred_at < ?
        AND (origin_account = ? OR dest_account = ?)
      ORDER BY occurred_at DESC, row_id DESC LIMIT ?
    """
    rows = connection.execute(
        query,
        [before - timedelta(hours=hours), before, account, account, limit],
    ).fetchall()
    names = [item[0] for item in connection.description]
    return tuple(_relative_transaction(dict(zip(names, row, strict=True)), account) for row in rows)


def _context(
    connection: duckdb.DuckDBPyConnection,
    row: dict[str, Any],
    *,
    source_scan: str,
    hours: int,
    limit: int,
) -> RuleContext:
    """Rebuild the live scorer context for one final-test transaction."""
    current = _relative_transaction(row, str(row["origin_account"]))
    return RuleContext(
        transaction=current,
        history=_history(
            connection,
            source_scan=source_scan,
            account=str(row["origin_account"]),
            before=current.occurred_at,
            hours=hours,
            limit=limit,
        ),
        counterparty_history=_history(
            connection,
            source_scan=source_scan,
            account=str(row["dest_account"]),
            before=current.occurred_at,
            hours=hours,
            limit=limit,
        ),
    )


def _prepared_case(  # noqa: PLR0913 - explicit production collaborators keep selection pure.
    *,
    row: dict[str, Any],
    dataset_name: str,
    context: RuleContext,
    scorer: Scorer,
    explainer: Explainer,
    cache: ModelCache,
    pointer: DeploymentPointer,
    retriever: RetrieverAdapter,
    ordinal: int,
) -> _Prepared | None:
    """Run the production analytical and prompt builders; return None when policy does not alert."""
    evaluation = RuleRegistry().evaluate(DEFAULT_RULE_DEFINITIONS, context)
    scored = scorer.score(pointer, context)
    score = ScoreResult(
        fraud_probability=scored.fraud_probability,
        model_version_label=scored.model_version_label,
        risk_thresholds=scored.risk_thresholds,
    )
    assessment = RiskPolicy().assess(
        fraud_probability=score.fraud_probability,
        rules_subscore=evaluation.subscore,
        model_thresholds=score.risk_thresholds,
    )
    if not assessment.alert:
        return None
    explanation = explainer.explain(cache.get(pointer), context)
    shap = ShapResult(
        base_value=explanation.base_value,
        shap_values=explanation.shap_values,
        top_features=tuple(
            SarFeature(feature=item.feature, value=item.value, shap_value=item.shap_value)
            for item in explanation.top_features
        ),
    )
    subject_key = str(row["origin_account"])
    row_fingerprint = hashlib.sha256(f"{dataset_name}:{row['row_id']}".encode()).hexdigest()
    pipeline_input = PipelineInput(
        agency_id="benchmark-synthetic",
        run_id=f"benchmark-{row_fingerprint[:16]}",
        transaction_id=row_fingerprint[:20],
        source="ibm-aml-synthetic",
        rule_context=context,
        amount=context.transaction.amount,
        currency=context.transaction.currency,
        country=context.transaction.country,
        channel=context.transaction.channel,
        feature_hash=hashlib.sha256(context.model_dump_json().encode()).hexdigest(),
    )
    rag = retriever.retrieve(build_rag_query(evaluation, pipeline_input), top_k=4)
    sar_input = build_sar_input(
        pipeline_input=pipeline_input,
        evaluation=evaluation,
        score=score,
        shap=shap,
        rag=rag,
        assessment=assessment,
    )
    typology_terms = tuple(
        dict.fromkeys(hit.rule_type.value.replace("_", " ") for hit in evaluation.hits)
    )
    case = build_benchmark_case(
        case_id=f"ibm-{dataset_name}-{ordinal:06d}-{row_fingerprint[:8]}",
        case_set="measured",
        source_dataset=dataset_name,
        sar_input=sar_input,
        required_facts=(
            str(sar_input.amount),
            sar_input.currency,
            sar_input.occurred_at.date().isoformat(),
            *typology_terms,
        ),
        expected_citation_ids=tuple(citation.citation for citation in sar_input.citations),
        history_length=len(context.history),
        payment_format=str(row["payment_format"]),
    )
    return _Prepared(case=case, sar_input=sar_input, subject_key=subject_key)


def _stratified(candidates: list[_Prepared], count: int) -> list[_Prepared]:
    """Round-robin deterministic candidates across all configured stratum dimensions."""
    buckets: dict[tuple[str, str, str, str], list[_Prepared]] = {}
    for item in candidates:
        case = item.case
        key = (
            case.payment_format,
            case.amount_band,
            case.history_length_band,
            case.prompt_length_band,
        )
        buckets.setdefault(key, []).append(item)
    selected: list[_Prepared] = []
    ordered = sorted(buckets)
    while len(selected) < count and ordered:
        next_round: list[tuple[str, str, str, str]] = []
        for key in ordered:
            if buckets[key] and len(selected) < count:
                selected.append(buckets[key].pop(0))
            if buckets[key]:
                next_round.append(key)
        ordered = next_round
    return selected


def _balanced(candidates: list[_Prepared], count: int) -> list[_Prepared]:
    """Exhaust distinct subjects before admitting repeat subjects, stratifying each pool."""
    unique: list[_Prepared] = []
    repeated: list[_Prepared] = []
    seen: set[str] = set()
    for candidate in candidates:
        target = unique if candidate.subject_key not in seen else repeated
        target.append(candidate)
        seen.add(candidate.subject_key)
    selected = _stratified(unique, count)
    if len(selected) < count:
        selected.extend(_stratified(repeated, count - len(selected)))
    return selected


def _retriever(repo_root: Path) -> RetrieverAdapter:
    """Bind the same baked offline RAG index and adapter used by the application pipeline."""
    settings = AppSettings()
    index = repo_root / settings.rag_index_dir
    embedder = HashingEmbedder(rag_version=settings.rag_version)
    if index_status(index, settings.rag_collection, embedder.provenance) != "ready":
        raise FileNotFoundError("offline RAG index is not ready; run make ingest-rag")
    return RetrieverAdapter(
        Retriever(
            persist_dir=index,
            collection=settings.rag_collection,
            embedder=embedder,
            min_similarity=settings.investigation_rag_min_similarity,
        )
    )


def _file_sha256(path: Path) -> str:
    """Hash a potentially large artifact without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_ibm_cases(
    config: VllmBenchConfig,
    *,
    profile: str,
    repo_root: Path,
) -> CaseArtifact:
    """Build the full IBM final-test corpus or stop with an explicit quota deficiency."""
    if profile != "full":
        raise ValueError("IBM final-test case generation requires profile=full")
    full_path = repo_root / config.cases.fulldata_config
    full = load_fulldata_config(full_path)
    if full.application_candidate != config.cases.application_candidate:
        raise ValueError("benchmark application candidate drifted from full-data protocol")
    application = full.dataset(full.application_candidate)
    try:
        evaluation = load_candidate_evaluation(full, application, repo_root)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "Phase 6 application-candidate artifact is unavailable; IBM case generation is on hold"
        ) from exc
    model_root = repo_root / full.paths.artifacts_dir
    pointer = DeploymentPointer(
        active_version_label=evaluation.model_bundle,
        active_artifact_uri=evaluation.model_bundle,
    )
    cache = ModelCache(model_root)
    scorer = Scorer(cache)
    explainer = Explainer(top_k=8)
    retriever = _retriever(repo_root)
    maximum_prompt = (config.server.max_model_len - config.request.max_tokens) * _CHARS_PER_TOKEN
    selected_all: list[_Prepared] = []
    auxiliary_pool: list[_Prepared] = []
    exclusions: list[CaseExclusion] = []
    upstream_folds: list[str] = []
    for dataset in full.datasets:
        required = config.cases.quotas.get(dataset.candidate, 0)
        if required <= 0:
            continue
        folds = folded_path(full, dataset, repo_root)
        if not folds.is_file():
            raise FileNotFoundError(
                f"full-data final-test artifacts are missing for {dataset.source}"
            )
        upstream_folds.append(_file_sha256(folds))
        source_scan = ingested_scan(full, dataset, repo_root)
        prepared: list[_Prepared] = []
        with duckdb.connect() as connection:
            rows = _candidate_rows(
                connection,
                fold_path=folds,
                source_scan=source_scan,
                seed=config.seed,
                limit=required * _CANDIDATE_MULTIPLIER,
            )
            for ordinal, row in enumerate(rows):
                context = _context(
                    connection,
                    row,
                    source_scan=source_scan,
                    hours=full.features.window_hours,
                    limit=full.features.history_max,
                )
                candidate = _prepared_case(
                    row=row,
                    dataset_name=dataset.candidate,
                    context=context,
                    scorer=scorer,
                    explainer=explainer,
                    cache=cache,
                    pointer=pointer,
                    retriever=retriever,
                    ordinal=ordinal,
                )
                if candidate is None:
                    continue
                if candidate.case.prompt_chars > maximum_prompt:
                    exclusions.append(
                        CaseExclusion(
                            source_dataset=dataset.candidate,
                            reason="context_limit",
                            prompt_chars=candidate.case.prompt_chars,
                        )
                    )
                    continue
                prepared.append(candidate)
        selected = _balanced(prepared, required)
        if len(selected) != required:
            detail = f"required {required}, eligible {len(selected)}"
            raise ValueError(f"IBM case quota deficient for {dataset.candidate}: {detail}")
        selected_all.extend(selected)
        selected_ids = {item.case.case_id for item in selected}
        auxiliary_pool.extend(item for item in prepared if item.case.case_id not in selected_ids)
    if len(selected_all) != config.cases.count:
        raise ValueError(
            f"IBM case corpus deficient: required {config.cases.count}, built {len(selected_all)}"
        )
    development_selected = _balanced(auxiliary_pool, config.cases.dev_count)
    if len(development_selected) != config.cases.dev_count:
        raise ValueError(
            f"IBM development quota deficient: required {config.cases.dev_count}, "
            f"eligible {len(development_selected)}"
        )
    development_ids = {item.case.case_id for item in development_selected}
    warmup_pool = [item for item in auxiliary_pool if item.case.case_id not in development_ids]
    warmup_selected = _balanced(warmup_pool, config.cases.warmup_count)
    if len(warmup_selected) != config.cases.warmup_count:
        raise ValueError(
            f"IBM warm-up quota deficient: required {config.cases.warmup_count}, "
            f"eligible {len(warmup_selected)}"
        )
    dev = tuple(
        item.case.model_copy(update={"case_id": f"dev-{index:04d}", "case_set": "development"})
        for index, item in enumerate(development_selected)
    )
    warmups = tuple(
        item.case.model_copy(update={"case_id": f"warmup-{index:03d}", "case_set": "warmup"})
        for index, item in enumerate(warmup_selected)
    )
    abstentions = tuple(
        build_benchmark_case(
            case_id=f"abstention-{index:03d}",
            case_set="abstention",
            source_dataset=item.case.source_dataset,
            sar_input=item.sar_input.model_copy(update={"citations": (), "rag_context": ""}),
            required_facts=item.case.required_facts,
            expected_citation_ids=(),
            history_length=0 if item.case.history_length_band == "none" else 1,
            payment_format=item.case.payment_format,
        )
        for index, item in enumerate(selected_all[: config.cases.abstention_fixtures])
    )
    subjects = [item.subject_key for item in selected_all]
    upstream = json.dumps(
        {
            "fullDataConfig": full.config_sha256,
            "model": evaluation.model_sha256,
            "folds": upstream_folds,
        },
        sort_keys=True,
    )
    prompt = SarPromptTemplate.load()
    return CaseArtifact(
        protocol_version=config.protocol_version,
        profile=profile,
        case_source="ibm-final-test",
        config_sha256=config.config_sha256,
        upstream_sha256=sha256_hex(upstream),
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.prompt_hash,
        distinct_subjects=len(set(subjects)),
        subject_overlap=len(subjects) - len(set(subjects)),
        cases=(*(item.case for item in selected_all), *dev, *warmups, *abstentions),
        exclusions=tuple(exclusions),
    )
