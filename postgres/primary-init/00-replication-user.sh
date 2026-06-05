#!/bin/sh
set -eu

REPL_USER="${REPLICATION_USER:-replicator}"
REPL_PASSWORD="${REPLICATION_PASSWORD:-${POSTGRES_PASSWORD:-}}"

if [ -z "${REPL_PASSWORD}" ]; then
  echo "REPLICATION_PASSWORD (or POSTGRES_PASSWORD) must be set" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v repl_user="$REPL_USER" \
  -v repl_password="$REPL_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I WITH REPLICATION LOGIN PASSWORD %L', :'repl_user', :'repl_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'repl_user')
\gexec
SELECT format('ALTER ROLE %I WITH REPLICATION LOGIN PASSWORD %L', :'repl_user', :'repl_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'repl_user')
\gexec
SQL
