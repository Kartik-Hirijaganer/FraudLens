"""Layering tests: fraudlens-ml must not import fraudlens-llm / fraudlens-backend."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from lib.docs_arch import render_module_map

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_PYPROJECT = REPO_ROOT / "packages" / "fraudlens-ml" / "pyproject.toml"
ML_SRC = REPO_ROOT / "packages" / "fraudlens-ml" / "src" / "fraudlens_ml"


def test_ml_pyproject_bans_llm_and_backend() -> None:
    config = tomllib.loads(ML_PYPROJECT.read_text(encoding="utf-8"))
    banned = config["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    assert "fraudlens_llm" in banned
    assert "fraudlens_backend" in banned


def test_module_map_drops_the_ml_to_llm_edge() -> None:
    mermaid = render_module_map()
    assert "ml -.may use.-> llm" not in mermaid
    assert "ml -. never imports .-x llm" in mermaid


def test_ruff_blocks_ml_importing_llm() -> None:
    probe = ML_SRC / "_layering_probe.py"
    probe.write_text('"""Layering probe (temporary)."""\nimport fraudlens_llm  # noqa: F401\n')
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        probe.unlink()
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "TID251" in output
    assert "fraudlens-llm" in output
