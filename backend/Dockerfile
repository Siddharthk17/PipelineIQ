FROM python:3.11.13-slim

# Security: create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Layer cache optimization: install deps before copying source
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source (context is backend/, copy into /app/backend/)
COPY . ./backend/

# Create upload/data/gunicorn-log directories and expose tests at /app/tests for compose exec parity
RUN mkdir -p /tmp/uploads /app/data /app/uploads /tmp/gunicorn && \
    ln -sfn /app/backend/benchmark /app/benchmark && \
    ln -sfn /app/backend/tests /app/tests && \
    ln -sfn /app/backend/scripts /app/scripts && \
    ln -sfn /app/backend/celery_app.py /app/celery_app.py && \
    chown -R appuser:appuser /app /tmp/uploads /tmp/gunicorn

# Switch to non-root user
USER appuser

# Expose API and SSE ports
EXPOSE 8000
EXPOSE 8001

# M-187: image-level health probe (compose per-service healthchecks override
# this for workers/beat/flower/sse; standalone `docker run` of the api/sse
# roles gets a working default). Python stdlib only — no curl in this image.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=10).status==200 else 1)"

CMD ["gunicorn", "backend.main:app", "--config", "backend/gunicorn.conf.py"]
