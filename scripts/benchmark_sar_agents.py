"""Summary: Thin staged CLI for the paired multi-agent SAR drafting evaluation.
`scenarios` is deterministic and free; `run` is the only API/provider-driving stage;
`judge` is the only direct judge-provider stage; `publish` validates completed local artifacts
before atomically committing hash-bound docs/frontend JSON; and `validate` rechecks that binding.
No stage is implicit.

Key classes:
- (none)

Key functions:
- main: dispatch the explicit evaluation stages with hard spending caps where applicable.

Notes:
- Bearer tokens and provider credentials are read from the environment and never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from fraudlens_llm import LlmClient, get_llm_settings, load_catalog
from fraudlens_ml.rag import load_corpus
from lib.sar_eval.config import DEFAULT_SAR_EVAL_CONFIG, load_sar_eval_config
from lib.sar_eval.judge import JudgePromptTemplate, load_judgments, run_judge_stage, write_judgments
from lib.sar_eval.publish import REPORT_BASENAME, publish_report, validate_published_artifacts
from lib.sar_eval.report import build_study_report, validate_report_binding
from lib.sar_eval.runner import load_api_runs, run_api_stage, write_api_runs
from lib.sar_eval.scenarios import generate_scenarios, load_scenarios, write_scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCENARIOS = "scenarios.json"
_RUNS = "runs.json"
_JUDGMENTS = "judgments.json"
_DOCS_DIR = Path("docs") / "reference" / "benchmarks"
_FRONTEND_JSON = Path("frontend") / "src" / "data" / "sar-multi-agent-study.json"


def _positive_decimal(value: str | None, name: str) -> Decimal:
    if not value:
        raise ValueError(f"{name} is required for this spending stage")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{name} must be a positive decimal")
    return parsed


def _validated_base_url(value: str, *, loopback_http_hosts: tuple[str, ...]) -> str:
    """Allow bearer auth only over HTTPS, except explicit loopback development."""
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("SAR_EVAL_BASE_URL must be a valid absolute URL") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("SAR_EVAL_BASE_URL must be an origin without credentials or a path")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in loopback_http_hosts
    ):
        raise ValueError("SAR_EVAL_BASE_URL must use HTTPS except for an explicit loopback origin")
    return value.rstrip("/")


def _run_dir(config_path: Path, run_id: str) -> Path:
    if re.fullmatch(r"sar-eval-[0-9a-f]{16}", run_id) is None:
        raise ValueError("run id must be a canonical sar-eval identifier")
    config = load_sar_eval_config(config_path)
    return REPO_ROOT / config.paths.output_dir / run_id


def _scenarios(config_path: Path) -> int:
    config_bytes = config_path.read_bytes()
    config = load_sar_eval_config(config_path)
    artifact = generate_scenarios(config, config_bytes)
    target = REPO_ROOT / config.paths.output_dir / artifact.run_id / _SCENARIOS
    write_scenarios(target, artifact)
    print(f"sar-eval-scenarios OK ({artifact.run_id}): 32 synthetic scenarios -> {target}")
    return 0


def _run(config_path: Path, run_id: str, *, retry_failed: bool) -> int:
    config = load_sar_eval_config(config_path)
    run_dir = _run_dir(config_path, run_id)
    scenarios = load_scenarios(run_dir / _SCENARIOS)
    base_url_value = os.environ.get("SAR_EVAL_BASE_URL", "").strip()
    token = os.environ.get("SAR_EVAL_AUTH_TOKEN", "").strip()
    if not base_url_value or not token:
        raise ValueError("SAR_EVAL_BASE_URL and SAR_EVAL_AUTH_TOKEN are required")
    base_url = _validated_base_url(
        base_url_value,
        loopback_http_hosts=config.api.loopback_http_hosts,
    )
    max_usd = _positive_decimal(os.environ.get("SAR_EVAL_RUN_MAX_USD"), "SAR_EVAL_RUN_MAX_USD")
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=config.api.timeout_s) as client:
        artifact = run_api_stage(
            scenarios,
            config,
            client=client,
            max_usd=max_usd,
            retry_failed=retry_failed,
        )
    write_api_runs(run_dir / _RUNS, artifact)
    print(f"sar-eval-run OK ({run_id}): 64 arm runs, spent ${artifact.spent_usd}")
    return 0


async def _judge_async(config_path: Path, run_id: str) -> int:
    config = load_sar_eval_config(config_path)
    run_dir = _run_dir(config_path, run_id)
    scenarios = load_scenarios(run_dir / _SCENARIOS)
    runs = load_api_runs(run_dir / _RUNS)
    max_usd = _positive_decimal(os.environ.get("SAR_EVAL_JUDGE_MAX_USD"), "SAR_EVAL_JUDGE_MAX_USD")
    catalog = load_catalog(get_llm_settings().catalog_path)
    prompt = JudgePromptTemplate.load(config.judge.prompt_id)
    artifact = await run_judge_stage(
        scenarios,
        runs,
        config,
        client=LlmClient.from_settings(),
        catalog=catalog,
        prompt=prompt,
        max_usd=max_usd,
    )
    write_judgments(run_dir / _JUDGMENTS, artifact)
    print(f"sar-eval-judge OK ({run_id}): 96 blind samples, spent ${artifact.spent_usd}")
    return 0


def _publish(config_path: Path, run_id: str) -> int:
    config = load_sar_eval_config(config_path)
    run_dir = _run_dir(config_path, run_id)
    scenarios = load_scenarios(run_dir / _SCENARIOS)
    runs = load_api_runs(run_dir / _RUNS)
    judgments = load_judgments(run_dir / _JUDGMENTS)
    corpus = load_corpus(REPO_ROOT / config.paths.corpus_dir)
    report = build_study_report(
        scenarios,
        runs,
        judgments,
        config,
        corpus_citation_ids={item.citation for item in corpus},
    )
    result = publish_report(
        report,
        docs_dir=REPO_ROOT / _DOCS_DIR,
        frontend_json_path=REPO_ROOT / _FRONTEND_JSON,
    )
    print(f"sar-eval-publish OK ({run_id}): report sha256 {result.report_sha256}")
    return 0


def _validate(config_path: Path) -> int:
    report_path = REPO_ROOT / _DOCS_DIR / f"{REPORT_BASENAME}.json"
    frontend_path = REPO_ROOT / _FRONTEND_JSON
    report = validate_published_artifacts(report_path, frontend_path)
    validate_report_binding(report, load_sar_eval_config(config_path))
    print("sar-eval-validate OK: committed report and frontend projection are SHA-256 bound")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch exactly one free, spending, or publication stage."""
    parser = argparse.ArgumentParser(description="Paired multi-agent SAR drafting evaluation.")
    parser.add_argument("--config", default=None, help="Protocol config override (tests only).")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("scenarios", help="Generate the deterministic 32-scenario matrix.")
    subcommands.add_parser("validate", help="Validate the committed report/projection binding.")
    for name in ("run", "judge", "publish"):
        command = subcommands.add_parser(name)
        command.add_argument("--run", required=True, help="Run id under the configured output dir.")
        if name == "run":
            command.add_argument(
                "--retry-failed",
                action="store_true",
                help="Explicitly retry terminal failed arms with fresh idempotency keys.",
            )
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else DEFAULT_SAR_EVAL_CONFIG
    try:
        if args.command == "scenarios":
            return _scenarios(config_path)
        if args.command == "validate":
            return _validate(config_path)
        if args.command == "run":
            return _run(config_path, args.run, retry_failed=args.retry_failed)
        if args.command == "judge":
            return asyncio.run(_judge_async(config_path, args.run))
        return _publish(config_path, args.run)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"sar-eval-{args.command} failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
