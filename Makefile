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
AML_DEMO_ROWS ?= 1600
AML_SAMPLE_ROWS ?= 50000

.PHONY: help install \
        backend-lint backend-format-check backend-typecheck backend-test backend-coverage backend-fmt backend-ci \
        frontend-lint frontend-format-check frontend-typecheck frontend-test frontend-coverage frontend-fmt frontend-ci \
        lint format-check typecheck test coverage fmt \
        lint-changed format-check-changed ci-changed \
        header-check llm-catalog-check secrets-scan no-hardcoding-check tenancy-check dup-check deadcode deps-audit docs docs-check openapi \
        backend-coverage-diff frontend-coverage-diff test-coverage-diff \
        version-next changelog-unreleased pr-summary release-gate local-release-check \
        run rebuild run-live local-demo local-demo-down local-demo-reset local-demo-smoke \
        db-migrate db-seed import-ieee ingest-aml-demo ingest-rag ingest-rag-live fetch-data train-model train-aml train-aml-sample activate-model retrain drift-scan tf-validate \
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
no-hardcoding-check: ## Flag hardcoded URLs/IPs/model-ids in source (rule 4 / §12.1).
	$(UV) run python scripts/check_no_hardcoding.py
tenancy-check: ## Assert every tenant-scoped table has indexed agency_id (plan §9.3).
	$(UV) run python scripts/check_tenancy.py
llm-catalog-check: ## Validate LLM catalog/provider schemas and trust metadata.
	$(UV) run python scripts/check_llm_catalog.py
dup-check: ## Copy/paste detection (jscpd).
	npx --yes jscpd@4 backend/src packages frontend/src --config .jscpd.json
deadcode: ## Dead-code sweep (warn-only; DEADCODE_STRICT=1 to fail).
	bash scripts/deadcode.sh
deps-audit: ## Dependency vulnerability audit (pip-audit + npm audit; needs network). Phase 13 gate.
	# --skip-editable: the local workspace packages are not on PyPI. --ignore-vuln: CVE-2026-45829
	# (ChromaDB HTTP-server /api/v2 RCE) is not exploitable here — we use the embedded/baked index,
	# not the server, and no fix is published; assessed in docs/runbooks/security.md §5.
	$(UV) run pip-audit --desc --skip-editable --ignore-vuln CVE-2026-45829
	cd $(FRONTEND) && $(NPM) audit --audit-level=high --omit=dev
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
pr-summary: ## Preview the auto PR area-summary for this branch (areas changed vs BASE_REF).
	$(UV) run python scripts/pr_summary.py --base $(BASE_REF) --summary-only
release-gate: ## Assert the §20 release gate (version consistency + automation wired); propose-only, never tags.
	$(UV) run python scripts/release_gate.py --format text
local-release-check: ci tf-validate docker-build local-demo-smoke release-gate ## Run the automatable local release/UAT gate; never tags/pushes.

# ---------------------------------------------------------------------------
# Image build (separate required check; proves the deploy image in CI)
# ---------------------------------------------------------------------------
docker-build: ## Build the backend image (no push).
	docker build -f backend/Dockerfile -t fraudlens-backend:local .

# ---------------------------------------------------------------------------
# Local demo & data lifecycle. `make run` is the clean one-command path: preserve/fetch IBM
# AML-Data -> Docker Postgres -> migrate + foundation seed -> masked ingest -> RAG -> production
# pipeline batch score -> gateway + frontend. The running application remains local/keyless.
# ---------------------------------------------------------------------------
local-demo: ## Boot the IBM-backed local stack; fetches via Infisical /ml when absent.
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/local_demo.py up
run: ## Clean-reset generated state, ingest IBM AML, pipeline-score, then boot locally.
	infisical run --env=prod --path=/ml -- env POSTGRES_PORT=$${POSTGRES_PORT:-55432} $(UV) run python scripts/local_demo.py rebuild
rebuild: ## Alias for `make run`.
	$(MAKE) run
run-live: ## Boot local dev against real Supabase/Postgres + OpenRouter via Infisical.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/local_demo.py live
local-demo-down: ## Stop the local demo stack and remove its containers.
	$(UV) run python scripts/local_demo.py down
local-demo-reset: ## Tear down the local demo and delete its volumes + local state.
	$(UV) run python scripts/local_demo.py reset
local-demo-smoke: ## Boot, hit the health probes, then tear down (local E2E gate).
	$(UV) run python scripts/local_demo.py smoke

db-migrate: ## Apply database migrations.
	$(UV) run alembic upgrade head
db-seed: ## Seed foundation identity/config/rules + the active fixture pointer (dev only).
	$(UV) run python scripts/seed.py
import-ieee: ## Import the committed synthetic IEEE-CIS sample.
	$(UV) run python scripts/import_ieee.py
ingest-aml-demo: ## Ingest a bounded real IBM AML prefix across three demo tenants.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/ingest_aml_demo.py --rows $(AML_DEMO_ROWS)
ingest-rag: ## Build the offline FinCEN/BSA hashing RAG index.
	$(UV) run python scripts/ingest_rag.py
ingest-rag-live: ## Build the live OpenRouter text-embedding-3-small RAG index.
	infisical run --env=prod --path=/llm -- env FRAUDLENS_RAG_EMBEDDING_MODE=live $(UV) run python scripts/ingest_rag.py
fetch-data: ## Fetch IBM AML-Data HI-Small via Infisical-injected Kaggle credentials.
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/fetch_dataset.py --source ibm-aml
train-model: ## Train + register the synthetic XGBoost candidate (CI/demo default).
	$(UV) run python scripts/train_model.py
train-aml: ## Train + register an IBM AML-Data candidate (active model is unchanged).
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/train_model.py --source ibm-aml
train-aml-sample: ## Fast real-data candidate smoke using a deterministic stratified sample.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/train_model.py --source ibm-aml --sample-rows $(AML_SAMPLE_ROWS)
activate-model: ## Promote the best gates-passed local model bundle to ACTIVE (dev only).
	$(UV) run python scripts/activate_model.py
retrain: ## Retrain a candidate from matured reviewed labels.
	$(UV) run python scripts/retrain.py
drift-scan: ## Run the advisory model drift scan.
	$(UV) run python scripts/drift_scan.py

tf-validate: ## Terraform fmt + validate (no backend) per environment (scaffolded/inert).
	terraform fmt -recursive -check infra/terraform
	@for env in dev prod; do \
		echo ">> terraform validate ($$env)"; \
		terraform -chdir=infra/terraform/environments/$$env init -backend=false -input=false -no-color >/dev/null; \
		terraform -chdir=infra/terraform/environments/$$env validate -no-color; \
	done

# ---------------------------------------------------------------------------
# Umbrella targets
# ---------------------------------------------------------------------------
ci: lint format-check typecheck coverage header-check llm-catalog-check secrets-scan no-hardcoding-check tenancy-check dup-check docs-check ## Read-only umbrella gate (mirrors CI).
pre-pr: fmt docs ci ## Format, regenerate docs, then run the full gate (the only writer).

upgrade: ## Update dependencies, then re-run the pre-PR gate (manual).
	$(UV) lock --upgrade
	cd $(FRONTEND) && $(NPM) update && $(NPM) audit fix || true
	$(MAKE) pre-pr

dev: ## Print the dev-server commands (run them in separate terminals).
	@echo "backend : uv run uvicorn fraudlens_backend.main:app --reload"
	@echo "frontend: npm --prefix $(FRONTEND) run dev"
