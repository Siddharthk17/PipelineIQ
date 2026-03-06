#!/bin/sh
set -e

echo "Running database migrations..."
python -m alembic upgrade head

echo "Seeding demo data..."
python -m backend.scripts.seed_demo || true

echo "Starting Celery worker in background..."
celery -A backend.celery_app:celery_app worker --loglevel=info --concurrency=2 &

echo "Starting API server on port ${PORT:-8000}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
