# syntax=docker/dockerfile:1.7
#
# Multi-stage, uv-based build for the FraudLens backend (plan §15.7 / Phase 14).
#
# Speed (build-once, promote-many): BASE_IMAGE defaults to python:3.11-slim-bookworm so the
# image is fully self-contained and `make docker-build` works on a clean checkout with no
# pre-existing base. The deploy pipeline overrides it with the weekly-prebuilt `fraudlens-base`
# (xgboost/shap/sklearn/langchain/chromadb already resident) — `--build-arg BASE_IMAGE=ghcr.io/
# <owner>/fraudlens-base:<sha>` — so per-commit builds only add the thin app layer (seconds).
# The uv cache mount + layered copies mean only changed layers rebuild/push.
#
# Reliability: the FinCEN/BSA ChromaDB index is BAKED in its own cached layer using the offline
# HashingEmbedder (no keys, no network) so prod (rag_index_required=true) is ready without a slow
# startup embed — keeping cold start within the ≤75s startup-probe budget. Model artifacts are
# baked as the committed fixture and overridden from Blob in prod (settings.model_artifacts_dir).
#
# Posture: members install as non-editable wheels; the runtime carries the venv + non-secret
# config + baked index + the fixture artifacts + the thin job entrypoints (Container Apps Jobs
# run retrain/batch-score from this same image). It runs uvicorn as a NON-ROOT user; secrets are
# never baked — they are injected at runtime from Infisical. Platform probes hit /healthz, /readyz.

ARG BASE_IMAGE=python:3.11-slim-bookworm

FROM ${BASE_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PYTHON_PREFERENCE=only-system
WORKDIR /app

# Manifests + lock, then source. The backend eagerly imports fraudlens-ml (scoring/SHAP/RAG/SAR)
# and fraudlens-llm (live SAR drafting) at startup, so all three members install as non-editable
# wheels with their FULL closure (fastapi, xgboost, shap, chromadb, langchain, provider clients) —
# the "everything heavy warm in one container" posture (§3.2). When BASE_IMAGE is the prebuilt
# fraudlens-base the uv cache is warm and this resolves in seconds; the default slim base does the
# full (still-cached) sync.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY packages/fraudlens-core/pyproject.toml packages/fraudlens-core/pyproject.toml
COPY packages/fraudlens-ml/pyproject.toml packages/fraudlens-ml/pyproject.toml
COPY packages/fraudlens-llm/pyproject.toml packages/fraudlens-llm/pyproject.toml
COPY backend/src backend/src
COPY packages/fraudlens-core/src packages/fraudlens-core/src
COPY packages/fraudlens-ml/src packages/fraudlens-ml/src
COPY packages/fraudlens-llm/src packages/fraudlens-llm/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    --package fraudlens-backend --package fraudlens-ml --package fraudlens-llm

# RAG index bake — runs entirely offline (HashingEmbedder, no API keys, no network). Its own
# stage/layer so it only re-runs when the corpus or ingest code changes (§15.7 layered caching).
FROM builder AS rag-index
ENV PATH="/app/.venv/bin:$PATH" \
    FRAUDLENS_CONFIG_DIR=/app/config \
    FRAUDLENS_RAG_INDEX_DIR=/app/rag-index
COPY config /app/config
COPY data/regulations /app/data/regulations
COPY scripts/ingest_rag.py /app/scripts/ingest_rag.py
RUN python scripts/ingest_rag.py

FROM python:3.11-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FRAUDLENS_ENVIRONMENT=prod \
    FRAUDLENS_CONFIG_DIR=/app/config \
    FRAUDLENS_RAG_INDEX_DIR=/app/.local/chroma
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config /app/config
# Baked RAG index (prod resolves FRAUDLENS_RAG_INDEX_DIR here; rag_index_required=true is satisfied).
COPY --from=rag-index --chown=app:app /app/rag-index /app/.local/chroma
# Committed fixture model + corpus (prod overrides the model dir from Blob) and the thin job
# entrypoints the Container Apps Jobs run (retrain.py, drift_scan.py, ...) — see infra/terraform/modules/jobs.
COPY --chown=app:app data /app/data
COPY --chown=app:app scripts /app/scripts
USER app
EXPOSE 8000
# `python -m uvicorn` (rather than the bare console script) is robust to PATH details.
CMD ["python", "-m", "uvicorn", "fraudlens_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
