# Infisical Secrets Migration

## Context

FraudLens currently names Akeyless as the required secrets manager, but the project is a
personal repository and Akeyless is not the practical fit. Infisical should become the
project-standard secrets manager for local development, CI/CD, and runtime secret delivery
while preserving the existing Aegis invariants: no committed secrets, no long-lived cloud
credentials in GitHub, no secrets in Terraform state, and PHI-safe operational behavior.

Official Infisical docs support this posture through local `infisical run` and GitHub
Actions OIDC with machine identities.

The Infisical project intentionally uses a single environment, `prod`, for this personal
project. Local development still uses synthetic data and non-secret local config, but
secret reads resolve from Infisical `prod`.

## Phase 1 — Repo Contract

- [x] Update the canonical agent/contributor rules to allow Infisical as the secrets
      source of truth and remove Akeyless-only wording.
- [x] Update configuration docs, architecture docs, Terraform notes, deploy runbooks, and
      PR checklist text to describe Infisical runtime secret delivery.
- [x] Update deploy workflow scaffolding to fetch Vercel secrets through Infisical OIDC
      instead of the Akeyless GitHub Action.
- [x] Update backend readiness placeholder naming and tests from `akeyless` to `infisical`.
- [x] Keep AWS SSM and Azure Key Vault out of the project secrets path unless explicitly
      introduced by a future plan.

## Phase 2 — Setup Runbook

- [x] Add an implementation-ready Infisical setup runbook covering project creation,
      environment/path conventions, local CLI usage, GitHub OIDC machine identity setup,
      repo variables, and secret inventory.
- [x] Make clear which values are safe to commit as GitHub variables and which values must
      only live inside Infisical.
- [x] Document that `.env` remains non-secret/local-only and that service tokens are not
      the preferred CI authentication path.
- [x] Simplify Infisical environment guidance to a single `prod` environment.
