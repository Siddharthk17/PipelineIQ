FROM python:3.11-slim

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY alembic.ini /app/alembic.ini

RUN mkdir -p /tmp/uploads /app/data /app/uploads /tmp/gunicorn && \
    ln -sfn /app/backend/tests /app/tests && \
    ln -sfn /app/backend/scripts /app/scripts && \
    ln -sfn /app/backend/celery_app.py /app/celery_app.py && \
    chown -R appuser:appuser /app /tmp/uploads /tmp/gunicorn

USER appuser

EXPOSE 8000
EXPOSE 8001

CMD ["gunicorn", "backend.main:app", "--config", "backend/gunicorn.conf.py"]
