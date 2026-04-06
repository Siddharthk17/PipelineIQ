#!/bin/sh
# Wait for API to be healthy before starting nginx
# This prevents the startup race condition where nginx tries to reach API before it's ready

API_HOST="${API_HOST:-api}"
API_PORT="${API_PORT:-8000}"
MAX_RETRIES="${MAX_RETRIES:-30}"
RETRY_INTERVAL="${RETRY_INTERVAL:-2}"

echo "Waiting for API at ${API_HOST}:${API_PORT} to be healthy..."

retries=0
while [ $retries -lt $MAX_RETRIES ]; do
    if wget -q -O /dev/null "http://${API_HOST}:${API_PORT}/health" 2>/dev/null; then
        echo "API is healthy! Starting nginx..."
        break
    fi
    retries=$((retries + 1))
    echo "Attempt $retries/$MAX_RETRIES - API not ready, waiting ${RETRY_INTERVAL}s..."
    sleep $RETRY_INTERVAL
done

if [ $retries -eq $MAX_RETRIES ]; then
    echo "WARNING: API did not become healthy after $MAX_RETRIES attempts. Starting nginx anyway..."
fi

# Execute the original nginx entrypoint
exec nginx -g 'daemon off;'