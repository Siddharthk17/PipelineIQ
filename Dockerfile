FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.11-slim AS production

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local

ENV PATH=/root/.local/bin:$PATH

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

COPY backend ./backend
COPY alembic.ini /app/alembic.ini

RUN mkdir -p /tmp/uploads /app/data /app/uploads /tmp/gunicorn && \
    ln -sfn /app/backend/tests /app/tests && \
    ln -sfn /app/backend/scripts /app/scripts && \
    ln -sfn /app/backend/celery_app.py /app/celery_app.py && \
    chown -R appuser:appuser /app /tmp/uploads /tmp/gunicorn

USER appuser

ENV PORT=8000
ENV WORKERS=4

EXPOSE 8000
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "backend.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "4", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "30"]
