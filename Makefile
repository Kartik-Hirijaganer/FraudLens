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
PY_SRC := backend/src packages/fraudlens-core/src packages/fraudlens-ml/src scripts

.PHONY: help install \
        backend-lint backend-format-check backend-typecheck backend-test backend-coverage backend-fmt backend-ci \
        frontend-lint frontend-format-check frontend-typecheck frontend-test frontend-coverage frontend-fmt frontend-ci \
        lint format-check typecheck test coverage fmt \
        header-check secrets-scan dup-check deadcode docs docs-check openapi test-coverage-diff \
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
# Cross-cutting checks
# ---------------------------------------------------------------------------
header-check: ## Validate top-of-file SUMMARY headers (rule 2).
	$(UV) run python scripts/check_headers.py
secrets-scan: ## gitleaks (whole repo) + Infisical/config guard (rule 4).
	gitleaks detect --no-banner --redact --no-git --source . --config .gitleaks.toml
	$(UV) run python scripts/check_no_secrets.py
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
test-coverage-diff: ## (CI) Coverage with the ≥90% gate (changed-file aware in CI).
	bash scripts/coverage.sh -q

# ---------------------------------------------------------------------------
# Image build (separate required check; proves the deploy image in CI)
# ---------------------------------------------------------------------------
docker-build: ## Build the backend image (no push).
	docker build -f backend/Dockerfile -t fraudlens-backend:local .

# ---------------------------------------------------------------------------
# Umbrella targets
# ---------------------------------------------------------------------------
ci: lint format-check typecheck coverage header-check secrets-scan dup-check docs-check ## Read-only umbrella gate (mirrors CI).
pre-pr: fmt docs ci ## Format, regenerate docs, then run the full gate (the only writer).

upgrade: ## Update dependencies, then re-run the pre-PR gate (manual).
	$(UV) lock --upgrade
	cd $(FRONTEND) && $(NPM) update && $(NPM) audit fix || true
	$(MAKE) pre-pr

dev: ## Print the dev-server commands (run them in separate terminals).
	@echo "backend : uv run uvicorn fraudlens_backend.main:app --reload"
	@echo "frontend: npm --prefix $(FRONTEND) run dev"
