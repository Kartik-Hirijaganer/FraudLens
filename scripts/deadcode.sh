#!/usr/bin/env bash
# Summary: Dead-code sweep across both stacks: vulture + ruff unused-symbol codes
#   (Python) and knip (frontend, when installed). WARN-ONLY by default so it never
#   blocks the dev loop on a false positive; set DEADCODE_STRICT=1 to fail on any
#   finding (useful as an opt-in CI signal). Findings are printed regardless.
# Notes: FastAPI route handlers / Pydantic models can look "unused" to vulture, hence
#   warn-only + decorator/min-confidence filters. Tune frontend/knip.json over time.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

strict="${DEADCODE_STRICT:-0}"
status=0

echo "== vulture (Python dead code) =="
uv run vulture backend/src packages/fraudlens-core/src packages/fraudlens-ml/src scripts \
  --min-confidence 80 --ignore-decorators "@router.*,@app.*,@api_router.*" || status=1

echo "== ruff unused symbols (F401/F811/F841) =="
uv run ruff check --select F401,F811,F841 backend packages scripts || status=1

echo "== knip (frontend dead code; skipped if not installed) =="
if (cd frontend && npx --no-install knip --version >/dev/null 2>&1); then
  (cd frontend && npx --no-install knip) || status=1
else
  echo "knip not installed; skipping (npm install in frontend to enable)."
fi

if [ "$status" -ne 0 ]; then
  if [ "$strict" = "1" ]; then
    echo "deadcode: findings above — failing because DEADCODE_STRICT=1."
    exit 1
  fi
  echo "deadcode: findings above are advisory (set DEADCODE_STRICT=1 to fail)."
fi
exit 0
