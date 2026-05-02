FROM python:3.11-slim

# Security: create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Layer cache optimization: install deps before copying source
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source (context is backend/, copy into /app/backend/)
COPY . ./backend/

# Copy alembic.ini to project root for migration commands
COPY alembic.ini /app/alembic.ini

# Create upload/data directories and expose tests at /app/tests for compose exec parity
RUN mkdir -p /tmp/uploads /app/data /app/uploads && \
    ln -sfn /app/backend/tests /app/tests && \
    ln -sfn /app/backend/scripts /app/scripts && \
    ln -sfn /app/backend/celery_app.py /app/celery_app.py && \
    chown -R appuser:appuser /app /tmp/uploads

# Switch to non-root user
USER appuser

# Expose API and SSE ports
EXPOSE 8000
EXPOSE 8001

CMD ["gunicorn", "backend.main:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--worker-connections", "1000", "--max-requests", "10000", "--max-requests-jitter", "1000", "--timeout", "30", "--graceful-timeout", "30", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info"]
