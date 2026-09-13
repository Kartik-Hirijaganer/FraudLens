#!/usr/bin/env bash
# Fail if any commit reachable from HEAD (or a single candidate message) attributes
# authorship to an AI agent. Enforces AGENTS.md Golden Rule 2: FraudLens is a personal
# portfolio repo and every commit is authored solely by the human owner. GitHub builds
# its Contributors sidebar from these trailers, so one leaked line is one too many.

set -euo pipefail

usage() {
    printf '%s\n' \
        "usage: make attribution-check                     # scan history (default: origin/main..HEAD, else all)" \
        "   or: bash scripts/check_no_ai_attribution.sh --message .git/COMMIT_EDITMSG" \
        "   or: RANGE=v0.1.0..HEAD bash scripts/check_no_ai_attribution.sh"
}

# Attribution patterns that must never reach origin. Kept deliberately broad: any
# co-author trailer naming a bot/model, and any tool-generated "Generated with" line.
pattern='(Co-[Aa]uthored-[Bb]y:.*(claude|anthropic|copilot|cursor|codex|chatgpt|openai|gemini|\[bot\]|bot@))|(noreply@anthropic\.com)|(Generated with \[?[Cc]laude)|(🤖)'

fail() {
    printf '\n%s\n' \
        "AI attribution found (AGENTS.md Golden Rule 2 — zero AI co-authorship)." \
        "Strip the offending trailer(s) and rewrite history before pushing:" \
        "  git rebase -i <base> --exec 'git commit --amend --no-edit'" \
        "Prevention: 'includeCoAuthoredBy: false' in .claude/settings.json and ~/.claude/settings.json." >&2
    exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

# commit-msg hook mode: validate a single candidate message file.
if [[ "${1:-}" == "--message" ]]; then
    msg_file="${2:-}"
    if [[ -z "$msg_file" || ! -f "$msg_file" ]]; then
        printf 'a readable message file is required after --message\n' >&2
        usage >&2
        exit 2
    fi
    if grep -nEi "$pattern" "$msg_file"; then
        fail
    fi
    printf 'Commit message OK: no AI attribution.\n'
    exit 0
fi

if (( $# > 0 )); then
    usage >&2
    exit 2
fi

# History mode. Default to the commits this branch would push; fall back to everything.
range="${RANGE:-}"
if [[ -z "$range" ]]; then
    if git rev-parse --verify --quiet origin/main >/dev/null; then
        range="origin/main..HEAD"
    else
        range="HEAD"
    fi
fi

# A literal marker line keeps each offending body line attributable to its commit.
# (NUL record separators are not portable to the BSD awk shipped with macOS.)
offenders="$(
    git log "$range" --format='@@FLCOMMIT@@%H%n%B' \
        | grep -Ei "^@@FLCOMMIT@@|$pattern" \
        | awk '/^@@FLCOMMIT@@/ { sha = substr($0, 13); next } { print sha "  " $0 }' \
        || true
)"

if [[ -n "$offenders" ]]; then
    printf '%s\n' "$offenders" >&2
    fail
fi

printf 'No AI attribution in %s.\n' "$range"
