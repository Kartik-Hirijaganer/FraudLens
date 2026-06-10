# syntax=docker/dockerfile:1.7
#
# Multi-stage, uv-based build for the FraudLens backend. Both stages share the
# python:3.11-slim base so the virtualenv built in the builder is binary-compatible
# with the runtime stage. Members are installed as non-editable wheels, so the
# runtime image carries only the venv + non-secret config (no source tree). The
# container runs uvicorn as a NON-ROOT user. Secrets are never baked in — they are
# injected at runtime from Akeyless. Platform probes hit /healthz and /readyz.

FROM python:3.11-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PYTHON_PREFERENCE=only-system
WORKDIR /app

# Manifests + lock, then source. `--package fraudlens-backend` installs the backend
# member and its FULL dependency closure (fastapi, uvicorn, pydantic, fraudlens-core)
# as non-editable wheels — plain `uv sync` would only sync the empty-dependency root.
# The uv cache mount makes rebuilds fast even though it is a single sync.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY packages/fraudlens-core/pyproject.toml packages/fraudlens-core/pyproject.toml
COPY packages/fraudlens-ml/pyproject.toml packages/fraudlens-ml/pyproject.toml
COPY backend/src backend/src
COPY packages/fraudlens-core/src packages/fraudlens-core/src
COPY packages/fraudlens-ml/src packages/fraudlens-ml/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package fraudlens-backend

FROM python:3.11-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FRAUDLENS_ENVIRONMENT=prod \
    FRAUDLENS_CONFIG_DIR=/app/config
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config /app/config
USER app
EXPOSE 8000
# `python -m uvicorn` (rather than the bare console script) is robust to PATH details.
CMD ["python", "-m", "uvicorn", "fraudlens_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
