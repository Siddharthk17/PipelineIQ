FROM python:3.11-slim

# Security: create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Layer cache optimization: install deps before copying source
COPY requirements.txt .
COPY vendor/ /tmp/vendor/
RUN pip install --no-cache-dir --upgrade pip --find-links /tmp/vendor/ && \
    pip install --no-cache-dir --no-index --find-links /tmp/vendor/ -r requirements.txt && \
    rm -rf /tmp/vendor/

# Copy application source (context is backend/, copy into /app/backend/)
COPY . ./backend/

# Copy alembic.ini to project root for migration commands
COPY alembic.ini /app/alembic.ini

# Create upload and data directories (use /tmp for ephemeral storage on Fly.io)
RUN mkdir -p /tmp/uploads /app/data && chown -R appuser:appuser /app /tmp/uploads

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run migrations, seed demo data, then start API and worker
CMD ["sh", "-c", "python -m alembic upgrade head && python -m backend.scripts.seed_demo || true && celery -A backend.celery_app:celery_app worker --loglevel=info --concurrency=1 & uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
