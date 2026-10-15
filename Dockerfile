# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra ocr --extra web --extra llm

COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra ocr --extra web --extra llm


FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    INVOICE_WEB_HOST=0.0.0.0 \
    INVOICE_LOG_FORMAT=json

USER app
WORKDIR /home/app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["invoice-to-excel", "serve"]
