#!/usr/bin/env bash
# Summary: Run the backend (pytest) test suite with coverage and the ≥90% gate
#   (the threshold lives in pyproject.toml's --cov-fail-under). Writes an HTML
#   report to htmlcov/ (gitignored) for local inspection. Frontend coverage is run
#   separately via vitest (see the Makefile frontend-coverage target).
# Notes: extra args are forwarded to pytest, e.g. `scripts/coverage.sh -k health`.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

exec uv run pytest --cov-report=term-missing --cov-report=html "$@"
