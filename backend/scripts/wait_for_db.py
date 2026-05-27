"""Wait for database to be ready, then run alembic migrations.

Replaces the inline shell retry loop in docker-compose with proper
connection-level retry through pgbouncer, handling the startup race
where pgbouncer accepts connections but hasn't established its pool
to postgres yet.
"""

import os
import subprocess
import sys
import time

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pipelineiq:pipelineiq@pgbouncer:5432/pipelineiq",
)
MAX_RETRIES = 30
RETRY_DELAY = 2  # seconds
BACKOFF_FACTOR = 1.3


def db_ready() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except psycopg2.OperationalError:
        return False


def main() -> int:
    last_error = None
    delay = RETRY_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        if db_ready():
            print(f"Database ready on attempt {attempt}/{MAX_RETRIES}")
            break
        print(
            f"Waiting for database (attempt {attempt}/{MAX_RETRIES}, "
            f"retrying in {delay:.0f}s)..."
        )
        time.sleep(delay)
        delay = min(delay * BACKOFF_FACTOR, 15)
        last_error = f"Could not connect after {attempt} attempts"
    else:
        print(f"FATAL: {last_error}", file=sys.stderr)
        return 1

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
