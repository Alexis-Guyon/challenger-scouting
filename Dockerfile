# Dockerfile — Challenger Scouting (backend + bundled static frontend)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg + bcrypt builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Backend deps
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Source
COPY backend /app/backend
COPY frontend /app/frontend

# Data dir for SQLite fallback (mounted as a volume in production)
RUN mkdir -p /app/backend/data

WORKDIR /app/backend

EXPOSE 8000

# DB migrations run on every start (idempotent)
CMD ["bash", "-c", "python migrate.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
