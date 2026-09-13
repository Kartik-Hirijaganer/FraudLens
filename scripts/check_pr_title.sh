#!/usr/bin/env bash
# Validate the exact PR title locally and in GitHub Actions from one policy definition.

set -euo pipefail

usage() {
    printf '%s\n' \
        "usage: PR_TITLE='feat: add health check' make pr-title-check" \
        "   or: bash scripts/check_pr_title.sh 'feat: add health check'"
}

if (( $# > 1 )); then
    usage >&2
    exit 2
fi

title="${1:-${PR_TITLE:-}}"
source_name="${1:+command argument}"
source_name="${source_name:-${PR_TITLE:+PR_TITLE}}"

if [[ -z "$title" ]] && command -v gh >/dev/null 2>&1; then
    if existing_title="$(gh pr view --json title --jq .title 2>/dev/null)"; then
        title="$existing_title"
        source_name="open PR for the current branch"
    fi
fi

if [[ -z "$title" ]] && [[ -t 0 ]]; then
    printf 'Proposed PR title: '
    IFS= read -r title
    source_name="interactive input"
fi

if [[ -z "$title" ]]; then
    printf '%s\n' \
        "PR title is required before the complete PR check can run." \
        "Pass PR_TITLE, run this from a branch with an open PR, or use an interactive terminal." >&2
    usage >&2
    exit 2
fi

pattern='^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([a-zA-Z0-9._/-]+\))?!?: .+$'
if [[ ! "$title" =~ $pattern ]]; then
    printf '%s\n' \
        "PR title must follow Conventional Commits, for example: feat: add health check" \
        "Actual PR title: $title" >&2
    exit 1
fi

printf 'PR title OK (%s): %s\n' "$source_name" "$title"
