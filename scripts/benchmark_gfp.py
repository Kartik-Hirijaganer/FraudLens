"""Summary: Thin CLI for the offline GFP tenant-isolation benchmark (GFP plan Phase 5;
serving boundary: ADR-017). `run` executes the frozen protocol end to end for every
configured dataset — verify the fetched file (NEVER download), servable-normalize,
node-induce + stratify the Medium variants, freeze folds, build Arm-A features via the
production loader, run the paired A/B/C x scope benchmark, curate the visual motifs
from the full-context global graph — and writes `study.json` + `motifs.json` under
`.local/gfp-study/<run-id>/`. `publish` validates a completed run (snapml engine,
complete grid, all three motifs, redaction) and atomically writes the committed report
+ frontend visual JSON. Both subcommands REFUSE `environment == "prod"`; nothing here
opens a database connection or touches the model registry/activation/artifact dirs.

Key classes:
- (none)

Key functions:
- main: CLI entry — run the benchmark or publish a completed run (prod-refused).

Notes:
- Dataset files must already exist under the protocol's data dir (`make fetch-gfp-data`);
  the benchmark fails fast on absent files rather than downloading.
- engine=snapml requires the installed snapml version to EQUAL the protocol pin —
  a mismatched engine invalidates the frozen protocol and aborts before any work.
- Tenant ownership reuses the study's own offline partitions (`RESEARCH_PARTITIONS` +
  `demo_agency_index`) — never a second ownership model (plan "Tenant ownership").
"""

from __future__ import annotations

import argparse
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

import fetch_dataset
from fraudlens_backend.settings import get_settings
from lib.aml_fraud import IBM_AML, build_feature_matrix, load_frame, servable_frame
from lib.gfp.benchmark import (
    DatasetBenchmarkInputs,
    DatasetBenchmarkResult,
    build_study_report,
    run_dataset_benchmark,
)
from lib.gfp.boundaries import DatasetProvenance, DatasetStudySpec
from lib.gfp.config import (
    DEFAULT_GFP_BENCHMARK_CONFIG,
    GfpBenchmarkConfig,
    GfpDatasetConfig,
    load_gfp_benchmark_config,
)
from lib.gfp.curation import CurationResult, curate_motifs
from lib.gfp.edges import GfpEdgeSet, build_gfp_edge_set, with_targets
from lib.gfp.fake import FakeGraphPreprocessor
from lib.gfp.materialize import EngineFactory
from lib.gfp.partitions import RESEARCH_PARTITIONS
from lib.gfp.publish import CuratedRunPayload, publish_run, write_run_artifacts
from lib.gfp.reference import ReferenceGraphPreprocessor
from lib.gfp.sampling import select_context_with_escalation, stratify_targets
from lib.gfp.schema import GraphFeatureConfig, GraphFeatureSchema
from lib.gfp.snapml_adapter import SnapMlGraphPreprocessor

REPO_ROOT = Path(__file__).resolve().parents[1]

_ENGINE_SNAPML = "snapml"
_ENGINE_REFERENCE = "reference"
_ENGINE_FAKE = "fake"
_ENGINES: tuple[str, ...] = (_ENGINE_SNAPML, _ENGINE_REFERENCE, _ENGINE_FAKE)
# The pure engines ship with the repo; only snapml carries an installable version.
_BUILTIN_ENGINE_VERSION = "builtin"
_FULL_CONTEXT = "full"

# Committed publication targets (the plan's "Contracts & deliverables" table).
_DOCS_BENCHMARKS_DIR = Path("docs") / "reference" / "benchmarks"
_FRONTEND_VISUAL_JSON = Path("frontend") / "src" / "data" / "gfp-tenant-isolation-study.json"


def _engine_runtime(
    name: str, feature_config: GraphFeatureConfig, pin: str
) -> tuple[str, EngineFactory]:
    """Resolve an engine name to (version, fresh-instance factory); enforce the snapml pin."""
    if name == _ENGINE_SNAPML:
        from importlib import metadata  # noqa: PLC0415 - only probed when snapml is requested

        installed = metadata.version("snapml")
        if installed != pin:
            raise RuntimeError(
                f"installed snapml {installed} does not match the frozen protocol pin {pin} — "
                "a mismatched engine invalidates published results"
            )
        return installed, lambda: SnapMlGraphPreprocessor(feature_config)
    if name == _ENGINE_REFERENCE:
        return _BUILTIN_ENGINE_VERSION, lambda: ReferenceGraphPreprocessor(feature_config)
    if name == _ENGINE_FAKE:
        return _BUILTIN_ENGINE_VERSION, lambda: FakeGraphPreprocessor(feature_config)
    raise ValueError(f"unknown engine '{name}' (choices: {list(_ENGINES)})")


def _resolve_dataset_frame(
    dataset: GfpDatasetConfig,
    paths: fetch_dataset.DatasetPaths,
    config: GfpBenchmarkConfig,
    agency_count: int,
) -> tuple[pd.DataFrame, Fraction | None]:
    """Return the servable context frame (+ the node-hash fraction actually used)."""
    if dataset.graph_context == _FULL_CONTEXT:
        return servable_frame(load_frame(paths, IBM_AML), IBM_AML), None

    def _fold_labels(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        edges = build_gfp_edge_set(
            servable_frame(frame, IBM_AML), config, agency_count=agency_count
        )
        return edges.labels, edges.folds

    ladder = tuple(Fraction(value) for value in config.sampling.node_hash_fractions)
    csv_path = Path(paths.directory) / paths.files[0].name
    selection = select_context_with_escalation(csv_path, ladder, _fold_labels)
    return servable_frame(selection.frame, IBM_AML), selection.fraction


def _dataset_provenance(
    dataset: GfpDatasetConfig,
    paths: fetch_dataset.DatasetPaths,
    edge_set: GfpEdgeSet,
    fraction: Fraction | None,
) -> DatasetProvenance:
    """Assemble the PHI-free provenance record for one prepared dataset."""
    targets = edge_set.is_target
    fold_target_counts = tuple(
        int((targets & (edge_set.folds == fold_id)).sum()) for fold_id in (0, 1, 2)
    )
    fold_target_positives = tuple(
        int(edge_set.labels[targets & (edge_set.folds == fold_id)].sum()) for fold_id in (0, 1, 2)
    )
    context_rows = int(edge_set.gfp_matrix.shape[0])
    return DatasetProvenance(
        spec=DatasetStudySpec(
            source=dataset.source,
            variant=paths.files[0].name,
            graph_context=dataset.graph_context,
            target_quota=dataset.target_quota,
        ),
        file_sha256=paths.files[0].sha256,
        source_row_count=paths.files[0].row_count,
        servable_row_count=context_rows,
        context_edge_count=context_rows,
        target_count=int(targets.sum()),
        node_hash_fraction=str(fraction) if fraction is not None else None,
        illicit_ratio=float(edge_set.labels.mean()),
        fold_assignment=edge_set.fold_assignment,
        fold_target_counts=(fold_target_counts[0], fold_target_counts[1], fold_target_counts[2]),
        fold_target_positive_counts=(
            fold_target_positives[0],
            fold_target_positives[1],
            fold_target_positives[2],
        ),
    )


def _sorted_base_features(
    frame: pd.DataFrame, edge_set: GfpEdgeSet, history_max: int
) -> np.ndarray:
    """Arm-A features via the production loader, re-ordered into GFP edge order."""
    features, labels = build_feature_matrix(frame, IBM_AML, history_max=history_max)
    sorted_features: np.ndarray = np.asarray(features)[edge_set.original_row_id]
    if not np.array_equal(labels[edge_set.original_row_id], edge_set.labels):
        raise RuntimeError(
            "Arm-A labels drifted from the edge-set labels — invariant violation; aborting"
        )
    return sorted_features


def _prepare_dataset(
    dataset: GfpDatasetConfig, config: GfpBenchmarkConfig, data_dir: Path, agency_count: int
) -> tuple[pd.DataFrame, GfpEdgeSet, DatasetProvenance]:
    """Verify, normalize, sample, fold, and target one configured dataset."""
    paths = fetch_dataset._verify_present(fetch_dataset.dataset_spec(dataset.source), data_dir)
    frame, fraction = _resolve_dataset_frame(dataset, paths, config, agency_count)
    edge_set = build_gfp_edge_set(frame, config, agency_count=agency_count)
    if dataset.target_quota is not None:
        quotas = (
            config.target_quotas.train,
            config.target_quotas.calibration,
            config.target_quotas.holdout,
        )
        targets = stratify_targets(edge_set.labels, edge_set.folds, quotas, seed=config.seed)
        edge_set = with_targets(edge_set, targets)
    return frame, edge_set, _dataset_provenance(dataset, paths, edge_set, fraction)


def _run(config_path: Path, engine_name: str) -> int:
    """Execute the full benchmark protocol and write the local run directory."""
    config = load_gfp_benchmark_config(config_path)
    feature_config = GraphFeatureConfig.from_benchmark(config)
    schema = GraphFeatureSchema.from_config(feature_config)
    engine_version, engine_factory = _engine_runtime(
        engine_name, feature_config, config.engine.version
    )
    settings = get_settings()
    data_dir = REPO_ROOT / config.paths.data_dir
    agency_count = len(RESEARCH_PARTITIONS)

    provenance: list[DatasetProvenance] = []
    results: list[DatasetBenchmarkResult] = []
    curation: CurationResult | None = None
    base_feature_count = 0
    for dataset in config.datasets:
        print(f">> gfp-benchmark: preparing {dataset.source} ({dataset.graph_context})")
        frame, edge_set, record = _prepare_dataset(dataset, config, data_dir, agency_count)
        base_features = _sorted_base_features(frame, edge_set, settings.investigation_history_max)
        base_feature_count = base_features.shape[1]
        collect_signals = dataset.graph_context == _FULL_CONTEXT and curation is None
        print(
            f">> gfp-benchmark: {dataset.source} context={record.context_edge_count} "
            f"targets={record.target_count} — training A/B/C x scopes"
        )
        result = run_dataset_benchmark(
            DatasetBenchmarkInputs(
                dataset_source=dataset.source,
                edge_set=edge_set,
                base_features=base_features,
                agency_count=agency_count,
                collect_curation_signals=collect_signals,
            ),
            config,
            schema,
            engine_factory,
        )
        if result.curation_signals is not None:
            curation = curate_motifs(edge_set, feature_config, result.curation_signals)
        provenance.append(record)
        results.append(result)

    if curation is None:
        raise RuntimeError("no full-context dataset produced curation signals — protocol error")
    report = build_study_report(
        config=config,
        config_sha256=sha256(config_path.read_bytes()).hexdigest(),
        engine_name=engine_name,
        engine_version=engine_version,
        schema=schema,
        base_feature_count=base_feature_count,
        datasets=tuple(provenance),
        results=tuple(results),
    )
    payload = CuratedRunPayload(
        agency_names=RESEARCH_PARTITIONS,
        missing_typologies=curation.missing_typologies,
        motifs=curation.motifs,
    )
    run_dir = REPO_ROOT / config.paths.output_dir / report.run_id
    write_run_artifacts(run_dir, report, payload)
    for comparison in report.comparisons:
        print(
            f">> gfp-benchmark: {comparison.dataset_source} isolationDelta "
            f"B={comparison.isolation_delta_b:+.4f} C={comparison.isolation_delta_c:+.4f}"
        )
    if curation.missing_typologies:
        print(
            f">> gfp-benchmark: WARNING — no real motif for {list(curation.missing_typologies)}; "
            "publication will fail until a run finds one"
        )
    print(
        f"gfp-benchmark OK ({report.run_id}): engine={engine_name} "
        f"datasets={len(report.datasets)} — publish with "
        f"`make gfp-publish GFP_RUN={report.run_id}`"
    )
    return 0


def _publish(config_path: Path, run_id: str) -> int:
    """Validate one completed run and atomically write the committed artifacts."""
    config = load_gfp_benchmark_config(config_path)
    run_dir = REPO_ROOT / config.paths.output_dir / run_id
    result = publish_run(
        run_dir,
        config=config,
        docs_dir=REPO_ROOT / _DOCS_BENCHMARKS_DIR,
        frontend_json_path=REPO_ROOT / _FRONTEND_VISUAL_JSON,
    )
    for path in (
        result.report_json_path,
        result.report_markdown_path,
        result.frontend_json_path,
    ):
        print(f">> gfp-publish: wrote {path.relative_to(REPO_ROOT)}")
    print(f"gfp-publish OK ({run_id}): report sha256 {result.report_sha256[:12]}...")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the offline GFP benchmark or publish a completed run."""
    parser = argparse.ArgumentParser(description="Offline GFP tenant-isolation benchmark.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run_parser = subcommands.add_parser("run", help="Execute the frozen benchmark protocol.")
    run_parser.add_argument(
        "--engine",
        choices=_ENGINES,
        default=_ENGINE_SNAPML,
        help="Graph engine (only snapml runs are publishable).",
    )
    run_parser.add_argument(
        "--config", default=None, help="Override the protocol pin file (tests only)."
    )
    publish_parser = subcommands.add_parser(
        "publish", help="Validate + atomically publish a completed run."
    )
    publish_parser.add_argument("--run", required=True, help="Run id under the output dir.")
    publish_parser.add_argument(
        "--config", default=None, help="Override the protocol pin file (tests only)."
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.environment == "prod":
        print("gfp-benchmark refused: the offline benchmark never runs in prod (ADR-017)")
        return 1
    config_path = Path(args.config) if args.config else DEFAULT_GFP_BENCHMARK_CONFIG
    try:
        if args.command == "run":
            return _run(config_path, args.engine)
        return _publish(config_path, args.run)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"gfp-{args.command} failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
