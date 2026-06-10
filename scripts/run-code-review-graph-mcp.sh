#!/usr/bin/env bash
# Summary: Launch the code-review-graph MCP server scoped to THIS repo (FraudLens).
#   Resolves the globally-installed `code-review-graph` binary, registers the repo,
#   builds the graph on first run / updates it on later runs, then serves over stdio.
#   Referenced by .mcp.json. Prereq: `uv tool install code-review-graph` (one-time).
# Notes: repo target is derived from this script's location (BASH_SOURCE), so it is
#   independent of the launcher's working directory. Graph cache lives in
#   <repo>/.code-review-graph/ (gitignored). No secrets, no network/LLM keys required.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

crg="$(command -v code-review-graph || true)"
if [[ -z "$crg" && -x "$HOME/.local/bin/code-review-graph" ]]; then
  crg="$HOME/.local/bin/code-review-graph"
fi
if [[ -z "$crg" ]]; then
  echo "code-review-graph not found. Install once with: uv tool install code-review-graph" >&2
  exit 1
fi

# Serial parsing avoids process-pool semaphore checks that sandboxed MCP launchers can block.
export CRG_SERIAL_PARSE=1

db="$repo_root/.code-review-graph/graph.db"
"$crg" register "$repo_root" --alias "$(basename "$repo_root")" >/dev/null 2>&1 || true
if [[ ! -f "$db" ]]; then
  "$crg" build --repo "$repo_root" --skip-flows >/dev/null 2>&1 \
    || echo "code-review-graph: initial build failed; serving with whatever graph exists." >&2
else
  "$crg" update --repo "$repo_root" --skip-flows >/dev/null 2>&1 || true
fi

exec "$crg" serve --repo "$repo_root"
