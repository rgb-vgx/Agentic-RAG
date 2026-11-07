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
RUN uv sync --no-dev --no-install-project

COPY . .
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
