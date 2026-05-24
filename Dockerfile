# En enda image som kan köra både webb och worker. Välj via CMD.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract + språkpacken för svenska, engelska och latin
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-swe \
        tesseract-ocr-eng \
        tesseract-ocr-lat \
        libtesseract-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Installera uv för snabb dependency-resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Layer-cache: kopiera bara pyproject.toml först
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY app/ ./app/
COPY seed_data/ ./seed_data/

# Skapa data-katalog
RUN mkdir -p /app/data/images/covers /app/data/images/thumbnails

# Defaults - kan ändras via docker run / compose
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Default: kör webben. Worker startas med `arq app.tasks.worker.WorkerSettings`
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
