FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

COPY apps/api/pyproject.toml /workspace/apps/api/pyproject.toml
COPY apps/api/transitpulse_api /workspace/apps/api/transitpulse_api
RUN pip install --editable "/workspace/apps/api[dev]"

COPY alembic.ini /workspace/alembic.ini
COPY migrations /workspace/migrations

EXPOSE 8000
