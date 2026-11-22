# syntax=docker/dockerfile:1
# Pinned versions; bump deliberately. Never use `latest`.
ARG PYTHON_IMAGE=python:3.14.10-alpine3.24
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1
ARG NODE_IMAGE=node:22.21.1-alpine3.22

FROM ${UV_IMAGE} AS uv

# ---- frontend: build the React app into static/app (Node is build-time only) ----
FROM ${NODE_IMAGE} AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend ./
RUN npm run build

# ---- build: resolve the locked deps into /app/.venv ----
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Deps first so source edits don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
COPY --from=frontend /app/src/kodex/static/app ./src/kodex/static/app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: venv only, unprivileged user ----
FROM ${PYTHON_IMAGE}
# antiword reads legacy .doc (~1 MB); LibreOffice is only a fallback when present.
RUN apk add --no-cache antiword
RUN addgroup -S app && adduser -S -G app -H -s /sbin/nologin app \
    && mkdir /data && chown app:app /data  # Morpheus SQLite; a fresh named volume copies this owner
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "kodex.main:app", "--host", "0.0.0.0", "--port", "8000"]
