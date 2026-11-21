# syntax=docker/dockerfile:1
# Complexity router: basal-serve running Remek/basal-omni-pico (CPU).
# Separate from the API image: torch has no musl wheels, so this one is glibc (slim),
# and the API image stays small Alpine without torch. Pinned versions; bump deliberately.
ARG PYTHON_IMAGE=python:3.14.10-slim-trixie

FROM ${PYTHON_IMAGE}
ARG TORCH_VERSION=2.11.0
ARG BASAL_VERSION=2.8.1
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url ${TORCH_INDEX} "torch==${TORCH_VERSION}" \
 && pip install "torch==${TORCH_VERSION}" \
        "basal @ https://github.com/rkinas/basal/archive/refs/tags/v${BASAL_VERSION}.tar.gz"
RUN groupadd -r app && useradd -r -g app -d /home/app -m -s /usr/sbin/nologin app \
 && install -d -o app -g app /home/app/.cache/huggingface  # a new named volume copies this ownership
USER app
# Weights (~1.5 GB) download here on first start; mount a volume to keep them.
ENV HF_HOME=/home/app/.cache/huggingface BASAL_MODEL=Remek/basal-omni-pico
EXPOSE 8000
# /health answers 503 {"status":"starting"} until the model is loaded (first start ~6 min).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["sh", "-c", "exec basal-serve --model \"$BASAL_MODEL\" --host 0.0.0.0 --port 8000"]
