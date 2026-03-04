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

# Create upload and data directories
RUN mkdir -p /app/uploads /app/data && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run migrations then start the app
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
