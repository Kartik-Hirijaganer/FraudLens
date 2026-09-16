#!/usr/bin/env bash
# Bind every FraudLens deploy to the personal identity that owns the repo and the Azure
# subscription, and refuse a deploy whose rendered manifests still carry placeholders.
#
# Why this exists: `gh auth status` lists both the personal and the work GitHub account, so
# deploying under the work identity is a live hazard rather than a theoretical one. This guard
# runs first in every deploy workflow and inside `make pre-pr`, so a wrong-account remote, a
# wrong commit email, a wrong Azure subscription/tenant, or an unresolved `replace-*` value
# fails before anything billable is created (AGENTS.md Golden Rules 2 and 7).
#
# Each check names its own evidence source. A check whose evidence cannot exist in the current
# context is reported as a SKIP with the reason, never silently passed: the local commit email
# is meaningless inside a GitHub Actions checkout that authors no commit, and `make pre-pr`
# must not require an Azure login.

set -euo pipefail

readonly EXPECTED_REPOSITORY="Kartik-Hirijaganer/FraudLens"
readonly EXPECTED_COMMIT_EMAIL="65550498+Kartik-Hirijaganer@users.noreply.github.com"
readonly EXPECTED_ORIGIN_URL="git@github-personal:Kartik-Hirijaganer/FraudLens.git"
readonly EXPECTED_SUBSCRIPTION_ID="01417138-33d6-4b26-a0e2-38090780d0ec"
readonly EXPECTED_TENANT_ID="057e49df-08cb-4abb-a4ee-469fd4f1954e"
readonly PLACEHOLDER_PATTERN='replace-[A-Za-z0-9_-]+'

usage() {
    printf '%s\n' \
        "usage: bash scripts/check_deploy_identity.sh [--rendered <path>]..." \
        "" \
        "Refuses any deploy that is not the personal Kartik-Hirijaganer/FraudLens identity." \
        "" \
        "  --rendered <path>  Rendered Terraform or Kubernetes output to scan for unresolved" \
        "                     'replace-*' placeholders. Repeatable. Committed overlays hold" \
        "                     placeholders by design, so only rendered output is scanned." \
        "" \
        "Environment:" \
        "  GITHUB_REPOSITORY        Repository slug; falls back to the 'origin' remote." \
        "  AZURE_SUBSCRIPTION_ID    Subscription under test; falls back to 'az account show'." \
        "  AZURE_TENANT_ID          Tenant under test; falls back to 'az account show'." \
        "  FRAUDLENS_RENDERED_MANIFESTS  Whitespace-separated rendered paths, as --rendered."
}

rendered_paths=()
while (( $# > 0 )); do
    case "$1" in
        --help | -h)
            usage
            exit 0
            ;;
        --rendered)
            if (( $# < 2 )); then
                printf 'error: --rendered requires a path\n' >&2
                usage >&2
                exit 2
            fi
            rendered_paths+=("$2")
            shift 2
            ;;
        *)
            printf 'error: unexpected argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Git refnames and these paths carry no spaces, so word-splitting the env list is safe.
if [[ -n "${FRAUDLENS_RENDERED_MANIFESTS:-}" ]]; then
    # shellcheck disable=SC2206  # intentional word-splitting of a whitespace-separated list.
    rendered_paths+=($FRAUDLENS_RENDERED_MANIFESTS)
fi

failures=()

pass() { printf '  OK    %s: %s\n' "$1" "$2"; }
skip() { printf '  SKIP  %s: %s\n' "$1" "$2"; }
fail() {
    printf '  FAIL  %s: %s\n' "$1" "$2"
    failures+=("$1: $2")
}

# --- repository ----------------------------------------------------------------------------
# GITHUB_REPOSITORY is authoritative inside Actions; locally the 'origin' remote is.
origin_url="$(git remote get-url origin 2>/dev/null || true)"
repository="${GITHUB_REPOSITORY:-}"
repository_source="GITHUB_REPOSITORY"
if [[ -z "$repository" ]]; then
    repository_source="origin remote"
    # Accept both SSH (alias or host) and HTTPS forms, then strip any .git suffix.
    repository="$(printf '%s\n' "$origin_url" \
        | sed -E 's#^(git@[^:]+:|ssh://[^/]+/|https?://[^/]+/)##; s#\.git$##')"
fi

if [[ -z "$repository" ]]; then
    fail repository "no GITHUB_REPOSITORY and no 'origin' remote to derive a slug from"
elif [[ "$repository" != "$EXPECTED_REPOSITORY" ]]; then
    fail repository "$repository is not $EXPECTED_REPOSITORY (source: $repository_source)"
else
    pass repository "$repository (source: $repository_source)"
fi

# --- commit email --------------------------------------------------------------------------
commit_email="$(git config user.email 2>/dev/null || true)"
if [[ -z "$commit_email" && "${GITHUB_ACTIONS:-}" == "true" ]]; then
    skip commit-email "unset in an Actions checkout, which authors no commit"
elif [[ "$commit_email" != "$EXPECTED_COMMIT_EMAIL" ]]; then
    fail commit-email "${commit_email:-<unset>} is not $EXPECTED_COMMIT_EMAIL"
else
    pass commit-email "$commit_email"
fi

# --- origin remote -------------------------------------------------------------------------
# The github-personal SSH alias is what makes a push authenticate as the personal account, so
# it is required locally. Actions checks out over its own HTTPS URL and cannot use the alias;
# there the slug check above is the binding constraint.
if [[ -z "$origin_url" ]]; then
    fail origin-remote "no 'origin' remote is configured"
elif [[ "$origin_url" == "$EXPECTED_ORIGIN_URL" ]]; then
    pass origin-remote "$origin_url"
elif [[ "${GITHUB_ACTIONS:-}" == "true" && "$origin_url" == *"$EXPECTED_REPOSITORY"* ]]; then
    skip origin-remote "Actions checkout uses $origin_url; the personal SSH alias cannot apply"
else
    fail origin-remote "$origin_url is not $EXPECTED_ORIGIN_URL"
fi

# --- Azure subscription and tenant ---------------------------------------------------------
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
azure_source="AZURE_SUBSCRIPTION_ID/AZURE_TENANT_ID"
if [[ -z "$subscription_id" || -z "$tenant_id" ]]; then
    azure_source="az account show"
    if command -v az >/dev/null 2>&1; then
        # A two-element projection prints one value per record; normalize either separator.
        azure_account="$(az account show --query '[id,tenantId]' -o tsv 2>/dev/null \
            | tr '\t' '\n' || true)"
        subscription_id="${subscription_id:-$(printf '%s\n' "$azure_account" | sed -n 1p)}"
        tenant_id="${tenant_id:-$(printf '%s\n' "$azure_account" | sed -n 2p)}"
    fi
fi

if [[ -z "$subscription_id" && -z "$tenant_id" ]]; then
    skip azure-account "no AZURE_* variables and no signed-in Azure CLI to read"
else
    if [[ "$subscription_id" != "$EXPECTED_SUBSCRIPTION_ID" ]]; then
        fail azure-subscription \
            "${subscription_id:-<unset>} is not $EXPECTED_SUBSCRIPTION_ID (source: $azure_source)"
    else
        pass azure-subscription "$subscription_id (source: $azure_source)"
    fi
    if [[ "$tenant_id" != "$EXPECTED_TENANT_ID" ]]; then
        fail azure-tenant \
            "${tenant_id:-<unset>} is not $EXPECTED_TENANT_ID (source: $azure_source)"
    else
        pass azure-tenant "$tenant_id (source: $azure_source)"
    fi
fi

# --- unresolved placeholders in rendered output --------------------------------------------
if (( ${#rendered_paths[@]} == 0 )); then
    skip rendered-placeholders "no rendered Terraform or Kubernetes output was supplied"
else
    for path in "${rendered_paths[@]}"; do
        if [[ ! -e "$path" ]]; then
            fail rendered-placeholders "$path does not exist"
            continue
        fi
        found="$(grep -rEoh "$PLACEHOLDER_PATTERN" "$path" 2>/dev/null | sort -u || true)"
        if [[ -n "$found" ]]; then
            fail rendered-placeholders \
                "$path still contains $(printf '%s' "$found" | tr '\n' ' ')"
        else
            pass rendered-placeholders "$path resolves every placeholder"
        fi
    done
fi

if (( ${#failures[@]} > 0 )); then
    printf '\n%s\n' "Refusing deploy: ${#failures[@]} identity check(s) failed." >&2
    for failure in "${failures[@]}"; do
        printf '  - %s\n' "$failure" >&2
    done
    printf '%s\n' \
        "FraudLens deploys only as $EXPECTED_REPOSITORY (AGENTS.md Golden Rules 2 and 7)." >&2
    exit 1
fi

printf '\ndeploy identity OK: %s\n' "$EXPECTED_REPOSITORY"
