# FraudLens — SINGLE SOURCE OF TRUTH for every check.
#
# The local pre-PR gate, GitHub Actions CI, and the deploy pre-gate all invoke the
# SAME targets here, so "if CI passes, deploy won't fail" is structural. `make ci`
# is read-only; `make pre-pr` (= fmt -> docs -> ci) is the only writer in the loop.

.DEFAULT_GOAL := help
SHELL := bash

UV ?= uv
NPM ?= npm
FRONTEND := frontend
PY_SRC := backend/src packages/fraudlens-core/src packages/fraudlens-llm/src packages/fraudlens-ml/src scripts

.PHONY: help install \
        backend-lint backend-format-check backend-typecheck backend-test backend-coverage backend-fmt backend-ci \
        frontend-lint frontend-format-check frontend-typecheck frontend-test frontend-coverage frontend-fmt frontend-ci \
        lint format-check typecheck test coverage fmt \
        lint-changed format-check-changed ci-changed \
        header-check llm-catalog-check secrets-scan dup-check deadcode docs docs-check openapi \
        backend-coverage-diff frontend-coverage-diff test-coverage-diff \
        version-next changelog-unreleased \
        docker-build ci pre-pr upgrade dev

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (uv workspace + frontend npm ci).
	$(UV) sync --all-packages
	cd $(FRONTEND) && $(NPM) ci

# ---------------------------------------------------------------------------
# Backend (Python) sub-targets
# ---------------------------------------------------------------------------
backend-lint:
	$(UV) run ruff check .
backend-format-check:
	$(UV) run ruff format --check .
backend-typecheck:
	$(UV) run mypy $(PY_SRC)
backend-test:
	$(UV) run pytest -q
backend-coverage:
	bash scripts/coverage.sh -q
backend-fmt:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .
backend-ci: backend-lint backend-format-check backend-typecheck backend-coverage ## Backend CI gate.

# ---------------------------------------------------------------------------
# Frontend (TypeScript) sub-targets
# ---------------------------------------------------------------------------
frontend-lint:
	cd $(FRONTEND) && $(NPM) run lint
frontend-format-check:
	cd $(FRONTEND) && $(NPM) run format:check
frontend-typecheck:
	cd $(FRONTEND) && $(NPM) run typecheck
frontend-test:
	cd $(FRONTEND) && $(NPM) run test
frontend-coverage:
	cd $(FRONTEND) && $(NPM) run coverage
frontend-fmt:
	cd $(FRONTEND) && $(NPM) run format
frontend-ci: frontend-lint frontend-format-check frontend-typecheck frontend-coverage ## Frontend CI gate.

# ---------------------------------------------------------------------------
# Cross-stack aggregates
# ---------------------------------------------------------------------------
lint: backend-lint frontend-lint ## Lint both stacks.
format-check: backend-format-check frontend-format-check ## Check formatting (no writes).
typecheck: backend-typecheck frontend-typecheck ## Type-check both stacks.
test: backend-test frontend-test ## Run tests for both stacks.
coverage: backend-coverage frontend-coverage ## Run tests with ≥90% coverage gate.
fmt: backend-fmt frontend-fmt ## Auto-format + autofix (WRITES; dev only).

# ---------------------------------------------------------------------------
# Changed-files gate — scopes per-file checks (ruff/eslint/prettier) to a PR's
# diff vs BASE_REF. Type-check + the full test suite intentionally stay repo-wide
# (see `ci`): scoping them to changed files would miss breakage in dependents that
# import a changed file. Coverage IS changed-file-aware via `test-coverage-diff`.
# ---------------------------------------------------------------------------
BASE_REF ?= origin/main
CHANGED_PY = $(UV) run python scripts/changed_files.py --category py --base $(BASE_REF)
CHANGED_TS = $(UV) run python scripts/changed_files.py --category ts --base $(BASE_REF) --relative-to $(FRONTEND)

lint-changed: ## Lint only files changed vs BASE_REF (ruff + eslint).
	@set -e; pyfiles="$$($(CHANGED_PY))"; \
	if [ -n "$$pyfiles" ]; then echo ">> ruff check (changed):"; $(UV) run ruff check $$pyfiles; \
	else echo ">> ruff check: no changed Python files"; fi
	@set -e; tsfiles="$$($(CHANGED_TS))"; \
	if [ -n "$$tsfiles" ]; then echo ">> eslint (changed):"; cd $(FRONTEND) && npx eslint $$tsfiles; \
	else echo ">> eslint: no changed TS files"; fi

format-check-changed: ## Check formatting on only files changed vs BASE_REF (ruff + prettier).
	@set -e; pyfiles="$$($(CHANGED_PY))"; \
	if [ -n "$$pyfiles" ]; then echo ">> ruff format --check (changed):"; $(UV) run ruff format --check $$pyfiles; \
	else echo ">> ruff format: no changed Python files"; fi
	@set -e; tsfiles="$$($(CHANGED_TS))"; \
	if [ -n "$$tsfiles" ]; then echo ">> prettier --check (changed):"; cd $(FRONTEND) && npx prettier --check $$tsfiles; \
	else echo ">> prettier: no changed TS files"; fi

ci-changed: lint-changed format-check-changed test-coverage-diff ## Changed-files PR gate (scoped to BASE_REF diff).

# ---------------------------------------------------------------------------
# Cross-cutting checks
# ---------------------------------------------------------------------------
header-check: ## Validate top-of-file SUMMARY headers (rule 2).
	$(UV) run python scripts/check_headers.py
secrets-scan: ## gitleaks (whole repo) + Infisical/config guard (rule 4).
	gitleaks detect --no-banner --redact --no-git --source . --config .gitleaks.toml
	$(UV) run python scripts/check_no_secrets.py
llm-catalog-check: ## Validate LLM catalog/provider schemas and trust metadata.
	$(UV) run python scripts/check_llm_catalog.py
dup-check: ## Copy/paste detection (jscpd).
	npx --yes jscpd@4 backend/src packages frontend/src --config .jscpd.json
deadcode: ## Dead-code sweep (warn-only; DEADCODE_STRICT=1 to fail).
	bash scripts/deadcode.sh
openapi: ## Fail if the committed OpenAPI is stale.
	$(UV) run python scripts/update_docs.py --check openapi
docs: ## Regenerate header inventories + OpenAPI + ERD + architecture AUTOGEN (WRITES).
	$(UV) run python scripts/update_docs.py
docs-check: ## Fail if any generated doc / header inventory is stale.
	$(UV) run python scripts/update_docs.py --check
backend-coverage-diff: ## Backend: ≥90% coverage on CHANGED lines (diff-cover, Cobertura).
	$(UV) run pytest -q --cov-report=xml --cov-fail-under=0
	$(UV) run diff-cover coverage.xml --compare-branch=$(BASE_REF) --fail-under=90
# frontend-coverage-diff: vitest writes frontend-relative SF: paths; we rewrite them to
# repo-relative (prepend frontend/) so diff-cover, run at the repo root, matches them
# against git's repo-relative diff paths.
frontend-coverage-diff: ## Frontend: ≥90% coverage on CHANGED lines (diff-cover, lcov).
	cd $(FRONTEND) && $(NPM) run coverage -- --coverage.reporter=lcov --coverage.reporter=text
	sed 's|^SF:|SF:$(FRONTEND)/|' $(FRONTEND)/coverage/lcov.info > $(FRONTEND)/coverage/lcov.repo.info
	$(UV) run diff-cover $(FRONTEND)/coverage/lcov.repo.info --compare-branch=$(BASE_REF) --fail-under=90
test-coverage-diff: backend-coverage-diff frontend-coverage-diff ## Changed-line coverage gate, both stacks (vs BASE_REF).

# ---------------------------------------------------------------------------
# Release helpers (propose-only — never tag/commit/push; Golden Rule 1). The
# `maintain` skill uses these to propose the SemVer bump + pending changelog.
# ---------------------------------------------------------------------------
version-next: ## Propose the next SemVer from Conventional Commits since the last tag (JSON).
	$(UV) run python scripts/next_version.py
changelog-unreleased: ## Render the pending changelog for the proposed version (git-cliff; stdout only).
	@set -e; tag="$$($(UV) run python scripts/next_version.py --format tag)"; \
	echo ">> pending changelog for $$tag:"; \
	uvx git-cliff --config cliff.toml --unreleased --tag "$$tag"

# ---------------------------------------------------------------------------
# Image build (separate required check; proves the deploy image in CI)
# ---------------------------------------------------------------------------
docker-build: ## Build the backend image (no push).
	docker build -f backend/Dockerfile -t fraudlens-backend:local .

# ---------------------------------------------------------------------------
# Umbrella targets
# ---------------------------------------------------------------------------
ci: lint format-check typecheck coverage header-check llm-catalog-check secrets-scan dup-check docs-check ## Read-only umbrella gate (mirrors CI).
pre-pr: fmt docs ci ## Format, regenerate docs, then run the full gate (the only writer).

upgrade: ## Update dependencies, then re-run the pre-PR gate (manual).
	$(UV) lock --upgrade
	cd $(FRONTEND) && $(NPM) update && $(NPM) audit fix || true
	$(MAKE) pre-pr

dev: ## Print the dev-server commands (run them in separate terminals).
	@echo "backend : uv run uvicorn fraudlens_backend.main:app --reload"
	@echo "frontend: npm --prefix $(FRONTEND) run dev"
