# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN pip install --no-cache-dir uv

# Dependencies only: the project is not a distributable package
# ([tool.uv] package = false), so --no-install-project is correct.
COPY pyproject.toml ./
# Docling is optional because it adds torch + layout/table/formula models
# (~6 GB). Enable for workers that must ingest scientific PDFs.
ARG INSTALL_DOCLING=false
RUN if [ "$INSTALL_DOCLING" = "true" ]; then \
        uv sync --no-dev --extra docling --no-install-project; \
    else \
        uv sync --no-dev --no-install-project; \
    fi

COPY . .
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
