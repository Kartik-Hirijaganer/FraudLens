"""Summary: Dataset, model, RAG, and portfolio-story subprocess steps for local demos.

Key classes:
- (none)

Key functions:
- start_postgres: start the local database.
- migrate_and_seed: establish the application schema and foundation data.
- fetch_ibm_demo_data: verify the configured IBM source.
- ingest_ibm_demo_data:
- activate_trained_model:
- score_ibm_demo_data:
- build_rag_index: build the governed offline regulation index.
- portfolio_story_environment:
- bootstrap_portfolio_demo: apply the configured synthetic story.
- backend_command:
- frontend_command:

Notes:
- Every subprocess runs from the repository root with an explicitly supplied environment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.demo_environment import DEFAULTS
from lib.demo_processes import compose_command

REPO_ROOT = Path(__file__).resolve().parents[2]


def start_postgres(env: dict[str, str]) -> None:
    """Start the compose Postgres in the background and wait for it to be healthy."""
    subprocess.run(compose_command("up", "-d", "--wait"), cwd=REPO_ROOT, env=env, check=True)


def migrate_and_seed(env: dict[str, str]) -> None:
    """Apply migrations then the foundation seed; a missing script is a broken checkout."""
    if not (REPO_ROOT / "alembic.ini").is_file():
        raise RuntimeError("Alembic config is missing; local demo cannot migrate the database")
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=REPO_ROOT, env=env, check=True)
    if not (REPO_ROOT / "scripts" / "seed.py").is_file():
        raise RuntimeError("foundation seed script is missing; local demo cannot seed identity")
    subprocess.run(["uv", "run", "python", "scripts/seed.py"], cwd=REPO_ROOT, env=env, check=True)


def fetch_ibm_demo_data(env: dict[str, str]) -> None:
    """Idempotently fetch/verify the real IBM AML dataset before local demo bootstrap."""
    script = REPO_ROOT / "scripts" / "fetch_dataset.py"
    if not script.is_file():
        raise RuntimeError(
            "IBM AML fetch script is missing; local demo cannot fall back to samples"
        )
    subprocess.run(
        ["uv", "run", "python", "scripts/fetch_dataset.py", "--source", "ibm-aml"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def ingest_ibm_demo_data(env: dict[str, str]) -> None:
    """Ingest a bounded, masked IBM AML partition into the freshly migrated local database."""
    script = REPO_ROOT / "scripts" / "ingest_aml_demo.py"
    if not script.is_file():
        raise RuntimeError("IBM AML demo ingest script is missing")
    subprocess.run(
        ["uv", "run", "python", "scripts/ingest_aml_demo.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def activate_trained_model(env: dict[str, str]) -> None:
    """Promote the best locally trained gates-passed bundle to ACTIVE (fixture stays otherwise).

    A gates-failed or absent bundle is never promoted; the script prints the honest outcome and
    the seeded fixture keeps serving, so a fresh clone still boots.
    """
    subprocess.run(
        ["uv", "run", "python", "scripts/activate_model.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def score_ibm_demo_data(env: dict[str, str]) -> None:
    """Batch-investigate the primary IBM demo partition through the production pipeline."""
    subprocess.run(
        ["uv", "run", "python", "-m", "fraudlens_backend.jobs.runner"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def build_rag_index(env: dict[str, str]) -> None:
    """Build the FinCEN/BSA RAG index; a missing script is a broken checkout, not a skip."""
    if not (REPO_ROOT / "scripts" / "ingest_rag.py").is_file():
        raise RuntimeError("RAG ingest script is missing; local demo cannot build the index")
    subprocess.run(
        ["uv", "run", "python", "scripts/ingest_rag.py"], cwd=REPO_ROOT, env=env, check=True
    )


def portfolio_story_environment(env: dict[str, str]) -> dict[str, str]:
    """Overlay what the portfolio story needs: its calibrated provider modes, and its own gate.

    Provider modes are not optional. The bootstrap refuses to run when the runtime `llm_mode` /
    `rag_embedding_mode` differ from the story's `execution:` block, and a RAG index built with one
    embedder cannot be queried with another — so the index build, the bootstrap, and the servers
    that later answer a visitor's live investigation must all agree. Identity and the database stay
    real; only the provider modes are pinned, and their values are READ from the story config
    rather than restated here.

    `portfolio_demo_enabled` is overlaid for the same reason. It defaults to False in code AND in
    `config/default.yaml` (a security gate fails closed), and live mode turns the dev bypass off,
    so without it `_projection_enabled` is False and `GET /api/v1/portfolio-demo/config` 404s: the
    login picker renders "personas unavailable" and a visitor would have to TYPE the synthetic
    password instead of clicking a persona. This command exists to serve that demo, so it asserts
    the gate exactly as `portfolio-demo-reset.yml` and `deploy-backend.yml`'s bootstrap step do.
    `live()` is untouched and still boots with the gate closed.
    """
    from fraudlens_backend.portfolio_demo import load_portfolio_demo_config  # noqa: PLC0415

    execution = load_portfolio_demo_config().execution
    return {
        **env,
        "FRAUDLENS_LLM_MODE": execution.llm_mode,
        "FRAUDLENS_RAG_EMBEDDING_MODE": execution.rag_embedding_mode,
        "FRAUDLENS_PORTFOLIO_DEMO_ENABLED": "true",
    }


def bootstrap_portfolio_demo(env: dict[str, str]) -> None:
    """Apply the configured portfolio demo story; a missing script is a broken checkout."""
    if not (REPO_ROOT / "scripts" / "bootstrap_portfolio_demo.py").is_file():
        raise RuntimeError("portfolio demo bootstrap script is missing")
    print(">> applying the configured portfolio demo story", flush=True)
    subprocess.run(
        ["uv", "run", "python", "scripts/bootstrap_portfolio_demo.py"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def backend_command(env: dict[str, str]) -> list[str]:
    """Build the uvicorn command for the gateway+services app."""
    return [
        "uv",
        "run",
        "uvicorn",
        "fraudlens_backend.main:app",
        "--host",
        "localhost",
        "--port",
        env.get("BACKEND_PORT", DEFAULTS["BACKEND_PORT"]),
    ]


def frontend_command(env: dict[str, str]) -> list[str]:
    """Build the Vite command for the SPA, pinning the selected local port."""
    return [
        "npm",
        "--prefix",
        "frontend",
        "run",
        "dev",
        "--",
        "--host",
        env.get("DEMO_HOST", DEFAULTS["DEMO_HOST"]),
        "--port",
        env.get("FRONTEND_PORT", DEFAULTS["FRONTEND_PORT"]),
        "--strictPort",
    ]
