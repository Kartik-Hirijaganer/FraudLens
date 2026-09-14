# FraudLens — SINGLE SOURCE OF TRUTH for every check.
#
# The local pre-PR gate, GitHub Actions CI, and the deploy pre-gate all invoke the
# SAME targets here, so "if CI passes, deploy won't fail" is structural. `make ci`
# is read-only; `make pre-pr` (= fmt -> docs -> ci) is the only writer in the loop.

.DEFAULT_GOAL := help
SHELL := bash

UV ?= uv
UVX ?= uvx
NPM ?= npm
DOCKER_PLATFORM ?= linux/amd64
FRONTEND := frontend
PY_SRC := backend/src packages/fraudlens-core/src packages/fraudlens-llm/src packages/fraudlens-ml/src scripts
# Row budget for `make ingest-aml-demo` ONLY. `make run` / `make local-demo` drive
# scripts/ingest_aml_demo.py through scripts/local_demo.py, which uses the script's own
# default — this knob does not change them.
AML_DEMO_ROWS ?= 1600
AML_SAMPLE_ROWS ?= 50000
FULLDATA_CANDIDATE ?= hi-small
FULLDATA_PILOT_ROWS ?= 1000000
FULLDATA := $(UV) run --group fulldata python scripts/fulldata.py
VLLM_BENCH := $(UV) run --group fulldata python scripts/benchmark_vllm.py
RUNPOD_GPU := $(UV) run python scripts/runpod_gpu.py
K8S_DEMO := $(UV) run python scripts/k8s_demo.py
KUBECTL ?= $(if $(wildcard .local/tools/kubectl),.local/tools/kubectl,kubectl)
KUBECONFORM ?= $(if $(wildcard .local/tools/kubeconform),.local/tools/kubeconform,kubeconform)
K8S_VERSION ?= $(shell PYTHONPATH=scripts $(UV) run python -c 'from lib.k8s_demo.config import load_config; print(load_config().kubernetes_version)')
K8S_DEMO_TESTS := tests/unit/test_k8s_demo_*.py tests/integration/test_k8s_manifests.py
PROFILE ?= smoke
SOURCE ?= sar-eval
HOST ?= runpod-rtx4090
PURCHASE ?= pay_as_you_go
TF_ROOTS ?= $(patsubst %/main.tf,%,$(wildcard infra/terraform/environments/*/main.tf))
DATA_BATCH_DIR := infra/terraform/environments/data-batch
DATA_BATCH_TFVARS := data-batch.tfvars
DATA_BATCH_PILOT_HOURS ?= 2
FULLDATA_DATA_DIR ?= .local/aml_data
FULLDATA_DOWNLOAD_DIR ?= .local/fulldata/downloads

define DATA_BATCH_ENV
subscription_id="$${TF_VAR_subscription_id:-$$(az account show --query id -o tsv)}"; \
tenant_id="$${TF_VAR_tenant_id:-$$(az account show --query tenantId -o tsv)}"; \
operator_cidr="$${TF_VAR_operator_cidr:-}"; \
if [ -z "$$operator_cidr" ]; then \
	operator_cidr="$$(curl -4 --fail --silent --show-error --max-time 10 https://api.ipify.org)/32"; \
fi; \
operator_principal_id="$${TF_VAR_operator_principal_id:-}"; \
operator_principal_type="$${TF_VAR_operator_principal_type:-}"; \
account_type="$$(az account show --query user.type -o tsv)"; \
if [ -z "$$operator_principal_type" ]; then \
	if [ "$$account_type" = "user" ]; then operator_principal_type="User"; else operator_principal_type="ServicePrincipal"; fi; \
fi; \
if [ -z "$$operator_principal_id" ]; then \
	if [ "$$account_type" = "user" ]; then \
		operator_principal_id="$$(az ad signed-in-user show --query id -o tsv)"; \
	else \
		account_client="$$(az account show --query user.name -o tsv)"; \
		operator_principal_id="$$(az ad sp show --id "$$account_client" --query id -o tsv)"; \
	fi; \
fi; \
ssh_key="$${TF_VAR_ssh_public_key:-}"; \
if [ -z "$$ssh_key" ]; then \
	ssh_key_file="$${TF_VAR_ssh_public_key_file:-$$HOME/.ssh/id_ed25519.pub}"; \
	test -f "$$ssh_key_file" || { echo "SSH public key not found: $$ssh_key_file"; exit 2; }; \
	ssh_key="$$(< "$$ssh_key_file")"; \
fi; \
budget_contacts="$${TF_VAR_budget_contact_emails:-}"; \
if [ -z "$$budget_contacts" ]; then \
	account_contact="$$(az account show --query user.name -o tsv)"; \
	budget_contacts="[\"$$account_contact\"]"; \
fi; \
shutdown_time="$${TF_VAR_auto_shutdown_time:-$$(python3 -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H%M"))')}"; \
budget_start="$${TF_VAR_budget_start_date:-$$(date -u +%Y-%m-01T00:00:00Z)}"; \
export TF_VAR_subscription_id="$$subscription_id" \
	TF_VAR_tenant_id="$$tenant_id" \
	TF_VAR_operator_cidr="$$operator_cidr" \
	TF_VAR_operator_principal_id="$$operator_principal_id" \
	TF_VAR_operator_principal_type="$$operator_principal_type" \
	TF_VAR_ssh_public_key="$$ssh_key" \
	TF_VAR_budget_contact_emails="$$budget_contacts" \
	TF_VAR_auto_shutdown_time="$$shutdown_time" \
	TF_VAR_budget_start_date="$$budget_start" \
	TF_VAR_use_oidc="$${TF_VAR_use_oidc:-false}" \
	TF_VAR_run_id="$${TF_VAR_run_id:-$(if $(RUN),$(RUN),data-batch-pending)}"
endef

.PHONY: iac-scan data-batch-quota data-batch-plan data-batch-up data-batch-upload \
	data-batch-download data-batch-ssh data-batch-start data-batch-down \
	data-batch-verify-clean data-batch-watchdog runpod-gpu-plan runpod-gpu-up \
	runpod-gpu-status runpod-gpu-ssh runpod-gpu-sync runpod-gpu-start runpod-gpu-stop \
	runpod-gpu-export runpod-gpu-down runpod-gpu-verify-clean runpod-gpu-test

.PHONY: k8s-tools-check k8s-validate k8s-demo-test kind-image kind-up kind-load \
	kind-deploy kind-smoke kind-hpa-demo kind-down kind-demo k8s-secrets-sync \
	hpa-evidence-validate

.PHONY: help install \
        backend-lint backend-format-check backend-typecheck backend-test backend-coverage backend-fmt backend-ci \
        postgres-run-test \
        frontend-lint frontend-format-check frontend-typecheck frontend-test frontend-coverage frontend-fmt frontend-ci \
        lint format-check typecheck test coverage fmt \
        lint-changed format-check-changed ci-changed \
        header-check file-length-check docs-links-check experiment-budget-check llm-catalog-check secrets-scan no-hardcoding-check demo-literals-check tenancy-check supabase-security-check dup-check deadcode deps-audit docs docs-check skills-check openapi scripts-test quality-gates fulldata-test \
        backend-coverage-diff frontend-coverage-diff test-coverage-diff \
        version-next changelog-unreleased pr-summary release-gate local-release-check \
        run rebuild run-live run-live-vllm run-live-demo local-demo local-demo-down local-demo-reset local-demo-smoke \
        portfolio-demo-bootstrap portfolio-demo-probe portfolio-demo-verify portfolio-demo-reset portfolio-demo-smoke \
        db-migrate db-seed import-ieee ingest-aml-demo ingest-rag ingest-rag-live fetch-data fetch-gfp-data gfp-container gfp-reference-test gfp-test gfp-benchmark gfp-publish sar-eval-scenarios sar-eval-run sar-eval-judge sar-eval-publish sar-eval-validate sar-eval-test train-model train-aml train-aml-sample activate-model batch-score retrain drift-scan fulldata-verify fulldata-ingest fulldata-features fulldata-parity fulldata-folds fulldata-train fulldata-evaluate fulldata-report fulldata-publish fulldata-validate fulldata-pilot fulldata-test tf-validate \
        vllm-bench-cases vllm-bench-cases-release vllm-bench-serve vllm-bench-stop vllm-bench-run vllm-bench-report vllm-bench-publish vllm-bench-test vllm-bench-validate \
        docker-build docker-build-base docker-build-base-if-changed \
        pr-title-check ci pre-pr pr-check worker upgrade dev

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

postgres-run-test: ## Prove durable claim/fencing behavior against PostgreSQL.
	@test -n "$${POSTGRES_TEST_DATABASE_URL:-}" || { echo "POSTGRES_TEST_DATABASE_URL is required"; exit 1; }
	$(UV) run pytest tests/integration/test_run_leases_postgres.py -q -o addopts='' -m postgres

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
file-length-check: ## Enforce the absolute 500-physical-line source cap.
	$(UV) run python scripts/check_file_length.py
docs-links-check: ## Validate every relative link in README, AGENTS, docs, and plans.
	$(UV) run python scripts/check_docs_links.py
experiment-budget-check: ## Reconcile the $75 experiment ceiling, ledger, and published run IDs.
	$(UV) run python scripts/experiment_budget.py ledger-check
attribution-check: ## Fail on AI co-author/attribution trailers in commits (Golden Rule 2).
	bash scripts/check_no_ai_attribution.sh
secrets-scan: ## gitleaks (whole repo) + Infisical/config guard (rule 4).
	gitleaks detect --no-banner --redact --no-git --source . --config .gitleaks.toml
	$(UV) run python scripts/check_no_secrets.py
no-hardcoding-check: ## Flag hardcoded URLs/IPs/model-ids in source (rule 4 / §12.1).
	$(UV) run python scripts/check_no_hardcoding.py
demo-literals-check: ## Flag portfolio-demo values restated outside config/portfolio-demo.yaml (rule 4).
	$(UV) run python scripts/check_no_demo_literals.py
tenancy-check: ## Assert every tenant-scoped table has indexed agency_id (plan §9.3).
	$(UV) run python scripts/check_tenancy.py
supabase-security-check: ## Audit live Supabase DB CIDRs + TLS (SUPABASE_PROJECT_REF required).
	@test -n "$(SUPABASE_PROJECT_REF)" || { echo "SUPABASE_PROJECT_REF is required"; exit 2; }
	$(UV) run python scripts/check_supabase_security.py --project-ref "$(SUPABASE_PROJECT_REF)"
llm-catalog-check: ## Validate LLM catalog/provider schemas and trust metadata.
	$(UV) run python scripts/check_llm_catalog.py
dup-check: ## Copy/paste detection (jscpd).
	npx --yes jscpd@4 backend/src packages frontend/src scripts --config .jscpd.json
deadcode: ## Dead-code sweep (warn-only; DEADCODE_STRICT=1 to fail).
	bash scripts/deadcode.sh
deps-audit: ## Dependency vulnerability audit (pip-audit + npm audit; needs network). Phase 13 gate.
	# --skip-editable: local workspace packages are not on PyPI. The four ChromaDB advisories
	# affect its unexposed HTTP/auth server, not FraudLens's embedded PersistentClient; no fixes
	# are published. Each exception is assessed in docs/runbooks/security.md §5.1.
	$(UV) run pip-audit --desc --skip-editable \
		--ignore-vuln CVE-2026-45829 \
		--ignore-vuln CVE-2026-45830 \
		--ignore-vuln CVE-2026-45831 \
		--ignore-vuln CVE-2026-45833
	cd $(FRONTEND) && $(NPM) audit --audit-level=high --omit=dev
openapi: ## Fail if the committed OpenAPI is stale.
	$(UV) run python scripts/update_docs.py --check openapi
docs: ## Regenerate the skill mirror, headers, OpenAPI, ERD, and architecture AUTOGEN (WRITES).
	$(UV) run python scripts/sync_skills.py
	$(UV) run python scripts/update_docs.py
skills-check: ## Validate project skills and fail if the generated Codex mirror is stale.
	$(UV) run python scripts/sync_skills.py --check
docs-check: skills-check ## Fail if any generated skill or documentation artifact is stale.
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
	docker build --platform $(DOCKER_PLATFORM) -f backend/Dockerfile -t fraudlens-backend:local .

docker-build-base: ## Build the linux/amd64 ML base image locally (no push).
	docker build --platform $(DOCKER_PLATFORM) -f backend/Dockerfile.base -t fraudlens-base:local .

docker-build-base-if-changed: ## Build the ML base only when its PR path-filter inputs changed.
	@set -eu; \
	base="$$(git merge-base "$(BASE_REF)" HEAD 2>/dev/null)" || { \
		echo "Cannot resolve BASE_REF=$(BASE_REF); fetch the base branch or pass BASE_REF=<ref>."; \
		exit 2; \
	}; \
	if git diff --quiet "$$base" -- backend/Dockerfile.base .github/workflows/build-base.yml uv.lock; then \
		echo ">> build-base: skipped (no path-filter inputs changed vs $(BASE_REF))"; \
	else \
		$(MAKE) docker-build-base; \
	fi

# ---------------------------------------------------------------------------
# Local demo & data lifecycle. `make run` is the clean one-command path: preserve/fetch IBM
# AML-Data -> Docker Postgres -> migrate + foundation seed -> activate the best gates-passed
# model bundle -> masked ingest -> RAG -> production pipeline batch score -> gateway + frontend.
# The running application remains local/keyless.
# ---------------------------------------------------------------------------
local-demo: ## Boot the IBM-backed local stack; fetches via Infisical /ml when absent.
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/local_demo.py up
run: ## Clean-reset generated state, ingest IBM AML, pipeline-score, then boot locally.
	infisical run --env=prod --path=/ml -- env POSTGRES_PORT=$${POSTGRES_PORT:-55432} $(UV) run python scripts/local_demo.py rebuild
rebuild: ## Alias for `make run`.
	$(MAKE) run
run-live: ## Boot local dev against real Supabase/Postgres + OpenRouter via Infisical.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/local_demo.py live
run-live-vllm: ## Boot the backend against self-hosted vLLM over a local SSH tunnel.
	infisical run --env=prod --path=/ml -- env FRAUDLENS_LLM_MODE=live FRAUDLENS_SAR_CONFIG_FILE=llm/sar-vllm.yml VLLM_BASE_URL=http://127.0.0.1:8000/v1 $(UV) run uvicorn fraudlens_backend.main:app --reload --host 127.0.0.1 --port $${BACKEND_PORT:-18000}
run-live-demo: ## Boot live dev AND bootstrap the exact portfolio demo story (mutating; prints the URL).
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/local_demo.py live-demo
worker: ## Run the durable investigation worker against the configured database.
	$(UV) run python -m fraudlens_backend.worker
local-demo-down: ## Stop the local demo stack and remove its containers.
	$(UV) run python scripts/local_demo.py down
local-demo-reset: ## Tear down the local demo and delete its volumes + local state.
	$(UV) run python scripts/local_demo.py reset
local-demo-smoke: ## Boot, hit the health probes, then tear down (local E2E gate).
	$(UV) run python scripts/local_demo.py smoke

# ---------------------------------------------------------------------------
# Portfolio demo story (config/portfolio-demo.yaml). Every target resolves the story
# location from the LAYERED settings (portfolio_demo_config_file), so none of them names
# a path; pass `--config` to the script directly for a one-off document. DATABASE_URL is
# supplied the same way `db-seed` and `activate-model` get it (env/.env locally).
# ---------------------------------------------------------------------------
PORTFOLIO_DEMO := $(UV) run python scripts/bootstrap_portfolio_demo.py

portfolio-demo-bootstrap: ## Apply (or resume) the configured portfolio demo story; idempotent.
	$(PORTFOLIO_DEMO)
portfolio-demo-probe: ## Report calibration (resolved policy + per-row p/r/codes/band); writes no run.
	$(PORTFOLIO_DEMO) --probe
portfolio-demo-verify: ## Read-only expected-vs-actual table; non-zero exit on any mismatch.
	$(PORTFOLIO_DEMO) --verify
portfolio-demo-reset: ## Delete the demo tenant's operational rows, then rebuild the pinned baseline.
	$(PORTFOLIO_DEMO) --reset
# `--no-cov`: the smoke selection deliberately exercises a REMOTE process, so the repo-wide
# --cov-fail-under=90 in `addopts` can never be met by it (the code under test runs elsewhere).
# Coverage stays enforced by `make coverage`, which runs the real suite in-process.
portfolio-demo-smoke: ## Run the smoke suite against a RUNNING demo (SMOKE_BASE_URL=<url> required).
	@test -n "$(SMOKE_BASE_URL)" || { echo "SMOKE_BASE_URL=<url> is required: this target boots nothing itself"; exit 1; }
	SMOKE_BASE_URL=$(SMOKE_BASE_URL) PORTFOLIO_DEMO_SMOKE_ENABLED=true $(UV) run pytest -m smoke --no-cov

db-migrate: ## Apply database migrations.
	$(UV) run alembic upgrade head
db-seed: ## Seed foundation identity/config/rules + the active fixture pointer (dev only).
	$(UV) run python scripts/seed.py
import-ieee: ## Import the committed synthetic IEEE-CIS sample (AGENCY_ID=<uuid> required).
	@test -n "$(AGENCY_ID)" || { echo "AGENCY_ID=<uuid> is required: the importer is tenant-generic"; exit 1; }
	$(UV) run python scripts/import_ieee.py --agency-id $(AGENCY_ID)
ingest-aml-demo: ## Ingest a bounded real IBM AML case pack into the configured demo tenant.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/ingest_aml_demo.py --rows $(AML_DEMO_ROWS)
ingest-rag: ## Build the offline FinCEN/BSA hashing RAG index.
	$(UV) run python scripts/ingest_rag.py
ingest-rag-live: ## Build the live OpenRouter text-embedding-3-small RAG index.
	infisical run --env=prod --path=/llm -- env FRAUDLENS_RAG_EMBEDDING_MODE=live $(UV) run python scripts/ingest_rag.py
fetch-data: ## Fetch IBM AML-Data HI-Small via Infisical-injected Kaggle credentials.
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/fetch_dataset.py --source ibm-aml
fetch-gfp-data: ## Fetch the three GFP-study variants one file at a time (Infisical Kaggle; skips present files).
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/fetch_dataset.py --source ibm-aml
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/fetch_dataset.py --source ibm-aml-hi-medium
	infisical run --env=prod --path=/ml -- $(UV) run python scripts/fetch_dataset.py --source ibm-aml-li-medium
train-model: ## Train + register the synthetic XGBoost candidate (CI/demo default).
	$(UV) run python scripts/train_model.py
train-aml: ## Train + register an IBM AML-Data candidate (active model is unchanged).
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/train_model.py --source ibm-aml
train-aml-sample: ## Fast real-data candidate smoke using a deterministic stratified sample.
	infisical run --env=prod --path=/ --recursive -- $(UV) run python scripts/train_model.py --source ibm-aml --sample-rows $(AML_SAMPLE_ROWS)
fulldata-verify: ## Verify all three IBM full-data files against frozen hashes and row counts.
	$(FULLDATA) verify
fulldata-ingest: ## Ingest one IBM source into typed Parquet (FULLDATA_CANDIDATE).
	$(FULLDATA) ingest --candidate $(FULLDATA_CANDIDATE)
fulldata-features: ## Build the 19 live-parity features for one IBM source.
	$(FULLDATA) features --candidate $(FULLDATA_CANDIDATE)
fulldata-parity: ## Run the mandatory live-builder parity sample for one source.
	$(FULLDATA) parity --candidate $(FULLDATA_CANDIDATE)
fulldata-folds: ## Materialize whole-cohort temporal folds for one source.
	$(FULLDATA) folds --candidate $(FULLDATA_CANDIDATE)
fulldata-train: ## Train/resume and evaluate one fixed IBM candidate.
	$(FULLDATA) train --candidate $(FULLDATA_CANDIDATE)
fulldata-evaluate: ## Bind every completed source candidate into one run manifest.
	$(FULLDATA) evaluate
fulldata-report: ## Render the current aggregate full-data report locally.
	$(FULLDATA) report
fulldata-publish: ## Validate and publish the current report to docs + frontend data.
	$(FULLDATA) publish
fulldata-validate: ## Revalidate the committed full-data report/frontend hash binding.
	$(FULLDATA) validate
fulldata-pilot: ## Run the approved bounded pilot (candidate + row target configurable).
	$(FULLDATA) pilot --candidate $(FULLDATA_CANDIDATE) --rows $(FULLDATA_PILOT_ROWS)

# ---------------------------------------------------------------------------
# vLLM BF16-versus-AWQ benchmark. Case validation and tests are provider-free;
# serve/run require an operator-selected GPU host. The full IBM corpus remains
# fail-closed until the Phase-6 application-candidate artifacts are available.
# ---------------------------------------------------------------------------
VLLM_CASES ?= .local/vllm-bench/cases-$(SOURCE)-$(PROFILE).json
VLLM_BENCH_TESTS := $(wildcard tests/unit/test_vllm_bench_*.py)

vllm-bench-cases: ## Build a deterministic case corpus (PROFILE + SOURCE).
	$(VLLM_BENCH) cases --profile "$(PROFILE)" --source "$(SOURCE)"
vllm-bench-cases-release: ## Attach full IBM cases to the RUN release (mutating; permission required).
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	@test "$(RELEASE_UPLOAD_APPROVED)" = "1" || { echo "RELEASE_UPLOAD_APPROVED=1 is required"; exit 2; }
	$(VLLM_BENCH) cases-release --run "$(RUN)" --confirm-upload
vllm-bench-serve: ## Start one pinned local vLLM arm (ARM=bf16|awq; GPU required).
	@test -n "$(ARM)" || { echo "ARM=bf16|awq is required"; exit 2; }
	$(VLLM_BENCH) serve --arm "$(ARM)"
vllm-bench-stop: ## Stop the configured local vLLM container.
	$(VLLM_BENCH) stop
vllm-bench-run: ## Run/resume one arm (RUN + ARM; server must already be ready).
	@test -n "$(ARM)" || { echo "ARM=bf16|awq is required"; exit 2; }
	$(VLLM_BENCH) run $(if $(RUN),--run "$(RUN)",) --arm "$(ARM)" \
		--profile "$(PROFILE)" --source "$(SOURCE)" \
		--host "$(HOST)" --purchase-option "$(PURCHASE)"
vllm-bench-report: ## Build the local report (RUN; VLLM_CASES may override the case path).
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(VLLM_BENCH) report --run "$(RUN)" --cases "$(VLLM_CASES)"
vllm-bench-publish: ## Publish an accepted full run (RUN; optional ALLOW_UNMET=1).
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(VLLM_BENCH) publish --run "$(RUN)" \
		$(if $(filter 1 true yes,$(ALLOW_UNMET)),--allow-unmet-acceptance,)
vllm-bench-test: ## Portable fake-server suite with >=90% benchmark-harness branch coverage.
	$(UV) run --group fulldata pytest $(VLLM_BENCH_TESTS) -q -o addopts='' \
		--cov=scripts/lib/vllm_bench --cov=benchmark_vllm --cov-branch \
		--cov-report=term-missing --cov-fail-under=90
vllm-bench-validate: ## Rebuild deterministic smoke cases and verify protocol/publication bindings.
	$(VLLM_BENCH) validate

# ---------------------------------------------------------------------------
# RunPod Secure Cloud RTX 4090 operator. Every lifecycle mutation is separately
# confirmed; the Pod exposes SSH only and carries an eight-hour self-stop guard.
# ---------------------------------------------------------------------------
RUNPOD_GPU_TESTS := $(wildcard tests/unit/test_runpod_gpu_*.py)

runpod-gpu-plan: experiment-budget-check ## Check live Secure Cloud capacity and budget admission.
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) plan --run "$(RUN)"
runpod-gpu-up: ## Create the admitted RunPod Pod (requires CONFIRM=yes and RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing RunPod creation: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) create --run "$(RUN)" --confirm-create
runpod-gpu-status: ## Show redacted status for an existing RunPod session.
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) status --run "$(RUN)"
runpod-gpu-ssh: ## Open full SSH to a ready identity-matched RunPod Pod.
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) ssh --run "$(RUN)"
runpod-gpu-sync: ## Sync committed source, cases, and vLLM token (CONFIRM=yes).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing RunPod sync: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	@test -f "$(VLLM_CASES)" || { echo "VLLM_CASES=$(VLLM_CASES) was not found"; exit 2; }
	$(RUNPOD_GPU) sync --run "$(RUN)" --cases "$(VLLM_CASES)" --confirm-sync
runpod-gpu-start: ## Start a stopped Pod (requires CONFIRM=yes and RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing RunPod start: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) start --run "$(RUN)" --confirm-start
runpod-gpu-stop: ## Stop a running Pod (requires CONFIRM=yes and RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing RunPod stop: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) stop --run "$(RUN)" --confirm-stop
runpod-gpu-export: ## Download and lineage-check benchmark artifacts before teardown.
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) export --run "$(RUN)"
runpod-gpu-down: ## Delete a stopped Pod and Pod volume (CONFIRM=yes; irreversible).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing RunPod deletion: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) delete --run "$(RUN)" --confirm-delete
runpod-gpu-verify-clean: ## Prove no matching Pod or network volume remains (read-only).
	@test -n "$(RUN)" || { echo "RUN=vllm-bench-<16 hex> is required"; exit 2; }
	$(RUNPOD_GPU) verify-clean --run "$(RUN)"
runpod-gpu-test: ## Provider-free RunPod operator tests with >=90% branch coverage.
	$(UV) run --group fulldata pytest $(RUNPOD_GPU_TESTS) -q -o addopts='' \
		--cov=scripts/lib/runpod_gpu --cov=runpod_gpu --cov=runpod_bench --cov-branch \
		--cov-report=term-missing --cov-fail-under=90

# ---------------------------------------------------------------------------
# Kubernetes demonstration. Every local mutation is guarded to the exact configured kind
# context by scripts/k8s_demo.py. The AKS secret path additionally requires CONFIRM=yes and a
# non-kind current context; no target here creates an Azure resource.
# ---------------------------------------------------------------------------
k8s-tools-check: ## Verify pinned local Kubernetes demonstration tools.
	$(K8S_DEMO) tools-check

k8s-validate: ## Render and statically validate every Kubernetes platform surface.
	@set -eu; \
	manifest_dir="$$(mktemp -d)"; \
	cleanup() { rm -rf "$$manifest_dir"; }; \
	trap cleanup EXIT INT TERM; \
	$(K8S_DEMO) render --platform kind > "$$manifest_dir/kind.yaml"; \
	$(K8S_DEMO) render --platform aks > "$$manifest_dir/aks.yaml"; \
	$(KUBECTL) kustomize deploy/k8s/load > "$$manifest_dir/load.yaml"; \
	cp deploy/k8s/addons/metrics-server-kind/components.yaml "$$manifest_dir/metrics.yaml"; \
	$(KUBECONFORM) -strict -kubernetes-version "$(K8S_VERSION)" \
		-skip InfisicalSecret -summary "$$manifest_dir"/*.yaml; \
	$(UVX) checkov --config-file .checkov.yaml --framework kubernetes \
		--directory "$$manifest_dir"
	$(UV) run pytest tests/integration/test_k8s_manifests.py -q -o addopts='' --no-cov

k8s-demo-test: ## Run the Kubernetes harness contract suite with branch coverage.
	$(UV) run pytest $(K8S_DEMO_TESTS) -q -o addopts='' \
		--cov=lib.k8s_demo --cov=k8s_demo --cov-branch \
		--cov-report=term-missing --cov-fail-under=90

kind-image: ## Build the backend image for the current host architecture (no push).
	@set -eu; \
	arch="$$(uname -m)"; \
	case "$$arch" in arm64|aarch64) platform=arm64 ;; x86_64|amd64) platform=amd64 ;; *) echo "unsupported host architecture: $$arch"; exit 2 ;; esac; \
	$(MAKE) docker-build DOCKER_PLATFORM=linux/$$platform

kind-up: ## Create the pinned zero-cost kind cluster and install metrics-server.
	$(K8S_DEMO) kind-up

kind-load: ## Load fraudlens-backend:local into the configured kind nodes.
	$(K8S_DEMO) kind-load

kind-deploy: ## Apply the kind overlay and wait for DB bootstrap, API, and worker.
	$(K8S_DEMO) deploy --platform kind

kind-smoke: ## Prove both ops probes via port-forward and run the remote smoke suite.
	$(K8S_DEMO) smoke

kind-hpa-demo: ## Run the HPA staircase and forced worker-kill durability proof.
	$(K8S_DEMO) hpa-demo

hpa-evidence-validate: ## Revalidate the committed Kubernetes scaling evidence.
	$(K8S_DEMO) evidence-validate

k8s-secrets-sync: ## Apply allowlisted runtime Secrets to an explicitly confirmed AKS context.
	@test "$(CONFIRM)" = "yes" || { echo "Refusing AKS secret mutation: pass CONFIRM=yes"; exit 2; }
	$(K8S_DEMO) secrets-sync --confirm-aks

kind-down: ## Delete the configured kind cluster and prove no backing containers remain.
	$(K8S_DEMO) kind-down

kind-demo: ## Build, deploy, smoke, prove HPA/durability, and always tear down local kind.
	@set -eu; \
	cleanup() { $(K8S_DEMO) kind-down; }; \
	trap cleanup EXIT INT TERM; \
	$(MAKE) kind-image; \
	$(K8S_DEMO) kind-up; \
	$(K8S_DEMO) kind-load; \
	$(K8S_DEMO) deploy --platform kind; \
	$(K8S_DEMO) smoke; \
	$(K8S_DEMO) hpa-demo; \
	trap - EXIT INT TERM; \
	cleanup

activate-model: ## Promote the best gates-passed local model bundle to ACTIVE (dev only).
	$(UV) run python scripts/activate_model.py
batch-score: ## Batch-investigate a tenant's un-scored rows (AGENCY_ID=<uuid>; defaults to the demo tenant).
	$(UV) run python -m fraudlens_backend.jobs.runner $(if $(AGENCY_ID),--agency-id $(AGENCY_ID),)
retrain: ## Retrain a candidate from matured reviewed labels.
	$(UV) run python scripts/retrain.py
drift-scan: ## Run the advisory model drift scan.
	$(UV) run python scripts/drift_scan.py

# ---------------------------------------------------------------------------
# GFP tenant-isolation benchmark (offline-only; ADR-017). snapml publishes no
# arm64-mac wheel, so on Apple Silicon benchmark commands run inside a THROWAWAY
# pinned Python-3.11 x86-64 container — a local compat wrapper, NOT a deployable
# image and NOT a CI job. Mounts: repo read-only, .local/ writable, fetched
# datasets read-only; uv state lives under .local/gfp-container/ so nothing
# escapes the gitignored scratch area.
# ---------------------------------------------------------------------------
GFP_CONTAINER_IMAGE ?= python:3.11-slim-bookworm
GFP_CONTAINER_UV ?= uv==0.11.28
# Portable GFP suites (reference/fake engines + a stubbed adapter — no snapml needed).
GFP_PORTABLE_TESTS := tests/unit/test_gfp_benchmark_config.py tests/unit/test_gfp_schema.py \
	tests/unit/test_gfp_boundaries.py tests/unit/test_gfp_edges.py \
	tests/unit/test_gfp_sampling_folds.py tests/unit/test_gfp_scopes.py \
	tests/unit/test_gfp_reference_engine.py tests/unit/test_gfp_engines.py \
	tests/unit/test_gfp_metrics.py tests/unit/test_gfp_benchmark.py \
	tests/unit/test_gfp_curation.py tests/unit/test_gfp_publish.py
gfp-reference-test: ## Portable GFP suite; >=90% branch coverage gate on scripts/lib/gfp (no snapml).
	$(UV) run pytest $(GFP_PORTABLE_TESTS) -q -o addopts='' \
		--cov=scripts/lib/gfp --cov-report=term-missing --cov-fail-under=90
gfp-test: ## Real snapml adapter parity (x86-64 only; FAILS — never skips — when snapml is absent).
	$(UV) sync --frozen --all-packages --group gfp
	GFP_REQUIRE_SNAPML=1 $(UV) run --no-sync pytest tests/unit/test_gfp_snapml_adapter.py -q -o addopts=''
gfp-benchmark: ## Full three-dataset snapml study run -> .local/gfp-study/<run-id>/ (x86-64; use gfp-container on arm64).
	$(UV) sync --frozen --all-packages --group gfp
	$(UV) run --no-sync python scripts/benchmark_gfp.py run
gfp-publish: ## Validate + atomically publish a completed run (GFP_RUN=<run-id>) to docs/ + frontend data.
	@if [ -z "$(GFP_RUN)" ]; then echo "usage: make gfp-publish GFP_RUN=<run-id>"; exit 2; fi
	$(UV) run python scripts/benchmark_gfp.py publish --run $(GFP_RUN)
gfp-container: ## Run CMD in a throwaway pinned Python-3.11 x86-64 container (arm64 compat; repo ro, .local rw, datasets ro).
	@if [ -z "$(CMD)" ]; then echo "usage: make gfp-container CMD='uv sync --frozen --group gfp && ...'"; exit 2; fi
	@mkdir -p .local/aml_data .local/gfp-container
	docker run --rm --platform linux/amd64 \
		-e GFP_CMD="$(CMD)" \
		-e UV_PROJECT_ENVIRONMENT=/repo/.local/gfp-container/venv \
		-e UV_CACHE_DIR=/repo/.local/gfp-container/uv-cache \
		-e UV_LINK_MODE=copy \
		-v "$(CURDIR):/repo:ro" \
		-v "$(CURDIR)/.local:/repo/.local" \
		-v "$(CURDIR)/.local/aml_data:/repo/.local/aml_data:ro" \
		-w /repo \
		"$(GFP_CONTAINER_IMAGE)" \
		bash -euo pipefail -c 'apt-get update -qq >/dev/null && apt-get install -qq -y --no-install-recommends libgomp1 make >/dev/null && python -m pip install --quiet "$(GFP_CONTAINER_UV)" && eval "$$GFP_CMD"'

# ---------------------------------------------------------------------------
# Paired multi-agent SAR evaluation (ADR-019). Scenario generation, tests, and
# publication are local-only. `run` and `judge` are the only provider-capable
# stages, require explicit hard USD caps, and receive secrets from Infisical prod.
# ---------------------------------------------------------------------------
SAR_EVAL := $(UV) run python scripts/benchmark_sar_agents.py
SAR_EVAL_RETRY_ARG := $(if $(filter 1 true yes,$(SAR_EVAL_RETRY_FAILED)),--retry-failed,)
SAR_EVAL_TESTS := tests/unit/test_sar_eval_cli.py \
	tests/unit/test_sar_eval_scenarios_metrics.py \
	tests/unit/test_sar_eval_runner.py \
	tests/unit/test_sar_eval_runner_contracts.py \
	tests/unit/test_sar_eval_judge.py \
	tests/unit/test_sar_eval_report_publish.py

sar-eval-scenarios: ## Generate the deterministic, synthetic 8x4 evaluation matrix (free).
	$(SAR_EVAL) scenarios
sar-eval-run: ## Run API arms (required: SAR_EVAL_RUN + cap; optional retry: SAR_EVAL_RETRY_FAILED=1; spends).
	@test -n "$(SAR_EVAL_RUN)" || { echo "SAR_EVAL_RUN=<run-id> is required"; exit 2; }
	@test -n "$(SAR_EVAL_RUN_MAX_USD)" || { echo "SAR_EVAL_RUN_MAX_USD=<hard-cap> is required"; exit 2; }
	infisical run --env=prod --path=/ --recursive -- env \
		SAR_EVAL_RUN_MAX_USD="$(SAR_EVAL_RUN_MAX_USD)" \
		$(SAR_EVAL) run --run "$(SAR_EVAL_RUN)" $(SAR_EVAL_RETRY_ARG)
sar-eval-judge: ## Run the blind cross-family judge (SAR_EVAL_RUN + SAR_EVAL_JUDGE_MAX_USD required; spends).
	@test -n "$(SAR_EVAL_RUN)" || { echo "SAR_EVAL_RUN=<run-id> is required"; exit 2; }
	@test -n "$(SAR_EVAL_JUDGE_MAX_USD)" || { echo "SAR_EVAL_JUDGE_MAX_USD=<hard-cap> is required"; exit 2; }
	infisical run --env=prod --path=/llm -- env \
		SAR_EVAL_JUDGE_MAX_USD="$(SAR_EVAL_JUDGE_MAX_USD)" \
		$(SAR_EVAL) judge --run "$(SAR_EVAL_RUN)"
sar-eval-publish: ## Validate and atomically publish one completed local run (SAR_EVAL_RUN required).
	@test -n "$(SAR_EVAL_RUN)" || { echo "SAR_EVAL_RUN=<run-id> is required"; exit 2; }
	$(SAR_EVAL) publish --run "$(SAR_EVAL_RUN)"
sar-eval-validate: ## Revalidate the committed docs/frontend report binding (free, providerless).
	$(SAR_EVAL) validate
sar-eval-test: ## Portable SAR evaluation suite with >=90% harness coverage; never calls providers.
	$(UV) run pytest $(SAR_EVAL_TESTS) -q -o addopts='' \
		--cov=scripts/lib/sar_eval --cov=benchmark_sar_agents \
		--cov-report=term-missing --cov-fail-under=90
	$(MAKE) sar-eval-validate

SCRIPTS_TESTS := tests/unit/test_aml_fraud.py \
	tests/integration/test_train_model.py tests/integration/test_train_model_cli.py \
	tests/unit/test_local_demo_environment.py tests/unit/test_local_demo_lifecycle.py \
	tests/unit/test_study_helpers.py tests/unit/test_quality_config.py \
	$(GFP_PORTABLE_TESTS) $(SAR_EVAL_TESTS)
scripts-test: ## Protect extracted script modules with >=90% aggregate branch coverage.
	$(UV) run pytest $(SCRIPTS_TESTS) -q -o addopts='' \
		--cov=lib.aml_fraud --cov=lib.demo_dataset_steps --cov=lib.demo_environment \
		--cov=lib.demo_processes --cov=lib.gfp --cov=lib.model_datasets \
		--cov=lib.model_training --cov=lib.quality --cov=lib.sar_eval --cov=lib.study \
		--cov=local_demo --cov=train_model --cov-branch \
		--cov-report=term-missing --cov-fail-under=90
	$(MAKE) sar-eval-validate

quality-gates: ## Run offline SAR citation, hallucination, and byte-level egress gates.
	$(UV) run pytest tests/quality -q -o addopts='' -m quality

FULLDATA_TESTS := tests/unit/test_fulldata_config_ingest.py \
	tests/unit/test_fulldata_features_folds.py tests/unit/test_fulldata_train_report.py \
	tests/unit/test_fulldata_cli.py
fulldata-test: ## Portable DuckDB full-data suite with >=90% harness branch coverage.
	$(UV) run --group fulldata pytest $(FULLDATA_TESTS) -q -o addopts='' \
		--cov=scripts/lib/fulldata --cov=fulldata --cov-branch \
		--cov-report=term-missing --cov-fail-under=90

data-batch-quota: ## Show the non-sensitive West US 3 quota decision inputs (read-only).
	@az vm list-usage --location westus3 \
		--query "[?localName=='Total Regional vCPUs' || localName=='Total Regional Low-priority vCPUs' || localName=='Standard EADSv5 Family vCPUs'].{quota:localName,current:currentValue,limit:limit}" \
		-o table

data-batch-plan: experiment-budget-check data-batch-quota ## Plan the PAYG CPU experiment without creating resources.
	@echo ">> projected two-pilot scheduling envelope ($(DATA_BATCH_PILOT_HOURS) PAYG hours)"
	@$(UV) run python scripts/experiment_budget.py estimate \
		--rate azure_e16ads_v5_payg --pilot-hours "$(DATA_BATCH_PILOT_HOURS)" \
		--pilot-units 1 --target-units 1
	@$(UV) run python scripts/experiment_budget.py admit --allocation azure_cpu_batch \
		--rate azure_e16ads_v5_payg --pilot-hours "$(DATA_BATCH_PILOT_HOURS)" \
		--pilot-units 1 --target-units 1
	@set -euo pipefail; \
	$(DATA_BATCH_ENV); \
	terraform -chdir=$(DATA_BATCH_DIR) init -backend=false -input=false -no-color >/dev/null; \
	terraform -chdir=$(DATA_BATCH_DIR) plan -input=false -lock=false -no-color \
		-var-file=$(DATA_BATCH_TFVARS)

data-batch-up: ## Create the approved data-batch session (requires CONFIRM=yes and RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing cloud mutation: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=data-batch-<session> is required"; exit 2; }
	@set -euo pipefail; \
	$(DATA_BATCH_ENV); \
	cp $(DATA_BATCH_DIR)/backend.tf.template $(DATA_BATCH_DIR)/backend.tf; \
	terraform -chdir=$(DATA_BATCH_DIR) init -reconfigure -input=false -no-color; \
	trap 'rm -f $(DATA_BATCH_DIR)/data-batch.tfplan' EXIT; \
	terraform -chdir=$(DATA_BATCH_DIR) plan -input=false -no-color \
		-var-file=$(DATA_BATCH_TFVARS) -out=data-batch.tfplan; \
	terraform -chdir=$(DATA_BATCH_DIR) apply -input=false -no-color data-batch.tfplan

data-batch-upload: ## Upload Medium CSVs after separate approval (CONFIRM=yes, RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing Blob upload: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=data-batch-<session> is required"; exit 2; }
	@test -f "$(FULLDATA_DATA_DIR)/HI-Medium_Trans.csv"
	@test -f "$(FULLDATA_DATA_DIR)/LI-Medium_Trans.csv"
	@set -euo pipefail; \
	account="$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw storage_account_name)"; \
	container="$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw storage_container_name)"; \
	az storage blob upload-batch --auth-mode login --account-name "$$account" \
		--destination "$$container/input/$(RUN)" --source "$(FULLDATA_DATA_DIR)" \
		--pattern '*-Medium_Trans.csv' --overwrite false

data-batch-download: ## Download exported artifacts for RUN (read-only cloud operation).
	@test -n "$(RUN)" || { echo "RUN=data-batch-<session> is required"; exit 2; }
	@mkdir -p "$(FULLDATA_DOWNLOAD_DIR)/$(RUN)"
	@set -euo pipefail; \
	account="$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw storage_account_name)"; \
	container="$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw storage_container_name)"; \
	az storage blob download-batch --auth-mode login --account-name "$$account" \
		--source "$$container" --destination "$(FULLDATA_DOWNLOAD_DIR)/$(RUN)" \
		--pattern "artifacts/$(RUN)/*" --overwrite false

data-batch-ssh: ## Connect to the existing data-batch VM.
	@ssh "$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw ssh_command | sed 's/^ssh //')"

data-batch-start: ## Restart a deallocated data-batch VM (requires CONFIRM=yes).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing cloud mutation: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@az vm start \
		--resource-group "$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw resource_group)" \
		--name "$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw vm_name)"

data-batch-watchdog: ## Inspect both shutdown safeguards on the existing VM.
	@echo ">> platform auto-shutdown: $$(terraform -chdir=$(DATA_BATCH_DIR) output -raw auto_shutdown_time) UTC"
	@ssh "$$(terraform -chdir=$(DATA_BATCH_DIR) output -raw ssh_command | sed 's/^ssh //')" \
		'sudo systemctl status fraudlens-watchdog.timer --no-pager; sudo systemctl list-timers fraudlens-watchdog.timer --no-pager'

data-batch-down: ## Destroy the approved data-batch session (requires CONFIRM=yes and RUN=...).
	@test "$(CONFIRM)" = "yes" || { echo "Refusing cloud teardown: rerun with CONFIRM=yes after explicit approval"; exit 2; }
	@test -n "$(RUN)" || { echo "RUN=data-batch-<session> is required"; exit 2; }
	@set -euo pipefail; \
	$(DATA_BATCH_ENV); \
	cp $(DATA_BATCH_DIR)/backend.tf.template $(DATA_BATCH_DIR)/backend.tf; \
	terraform -chdir=$(DATA_BATCH_DIR) init -reconfigure -input=false -no-color; \
	terraform -chdir=$(DATA_BATCH_DIR) destroy -input=false -no-color -auto-approve \
		-var-file=$(DATA_BATCH_TFVARS)

data-batch-verify-clean: ## Prove no tagged data-batch resource, RG, or budget remains (read-only).
	@set -euo pipefail; \
	failed=0; \
	if [ "$$(az group exists --name fraudlens-data-batch-rg)" != "false" ]; then \
		echo "residue: resource group fraudlens-data-batch-rg exists"; failed=1; \
	fi; \
	resource_count="$$(az resource list \
		--query "length([?starts_with(name, 'fraudlens-data-batch') || tags.environment == 'data-batch'])" -o tsv)"; \
	if [ "$$resource_count" != "0" ]; then \
		echo "residue: $$resource_count tagged/prefixed Azure resources"; \
		az resource list --query "[?starts_with(name, 'fraudlens-data-batch') || tags.environment == 'data-batch'].{name:name,type:type,resourceGroup:resourceGroup}" -o table; \
		failed=1; \
	fi; \
	if az consumption budget show --budget-name fraudlens-data-batch-budget \
		--only-show-errors --output none >/dev/null 2>&1; then \
		echo "residue: subscription budget fraudlens-data-batch-budget exists"; failed=1; \
	fi; \
	test "$$failed" = "0" || exit 1; \
	echo "data-batch-verify-clean OK: no resource group, tagged resource, or budget remains"

iac-scan: ## Scan Terraform for security and configuration defects (read-only).
	$(UVX) checkov --config-file .checkov.yaml

tf-validate: ## Terraform fmt + validate (no backend) per discovered environment root.
	terraform fmt -recursive -check infra/terraform
	@for root in $(TF_ROOTS); do \
		echo ">> terraform validate ($$(basename $$root))"; \
		terraform -chdir=$$root init -backend=false -input=false -no-color >/dev/null; \
		terraform -chdir=$$root validate -no-color; \
	done

# ---------------------------------------------------------------------------
# Umbrella targets
# ---------------------------------------------------------------------------
pr-title-check: ## Validate PR_TITLE, an existing PR title, or an interactively entered title.
	bash scripts/check_pr_title.sh

ci: lint format-check typecheck coverage header-check file-length-check docs-links-check experiment-budget-check attribution-check llm-catalog-check secrets-scan no-hardcoding-check demo-literals-check tenancy-check dup-check docs-check scripts-test quality-gates fulldata-test vllm-bench-test vllm-bench-validate runpod-gpu-test k8s-validate k8s-demo-test ## Read-only umbrella gate (mirrors CI).
pre-pr: fmt docs ci ## Format, regenerate docs, then run the shared CI umbrella (writes).

pr-check: ## Complete local PR preflight; mirrors all applicable GitHub PR checks (writes).
	$(MAKE) pr-title-check
	$(MAKE) install
	$(MAKE) pre-pr
	$(MAKE) ci-changed BASE_REF="$(BASE_REF)"
	$(MAKE) docker-build
	$(MAKE) tf-validate
	$(MAKE) iac-scan
	$(MAKE) deps-audit
	$(MAKE) docker-build-base-if-changed BASE_REF="$(BASE_REF)"
	@echo ">> PR preflight passed"

upgrade: ## Update dependencies, then re-run the pre-PR gate (manual).
	$(UV) lock --upgrade
	cd $(FRONTEND) && $(NPM) update && $(NPM) audit fix || true
	$(MAKE) pre-pr

dev: ## Print the dev-server commands (run them in separate terminals).
	@echo "backend : uv run uvicorn fraudlens_backend.main:app --reload"
	@echo "frontend: npm --prefix $(FRONTEND) run dev"
