#!/usr/bin/env bash
#
# Restore a PostgreSQL backup created by scripts/backup.sh.
#
# This is the drill half of the backup story — a backup you have never
# restored is a rumor. Practice on a scratch clone before you need it.
#
# Usage:
#   ./scripts/restore.sh backups/pg-2026-01-02-0200.sql.gz [--yes]
#
# What it does:
#   1. Refuses to run without --yes unless you confirm interactively
#      (restore REPLACES the live database).
#   2. Stops writers (api, workers, beat, sse) so nothing writes mid-restore.
#   3. Drops + recreates the database, loads the dump, runs alembic upgrade
#      head (in case the dump predates the current schema).
#   4. Restarts the stack and verifies /health.
#
# MinIO objects are NOT part of pg_dump. If uploads/outputs matter, mirror
# the buckets separately (see DEPLOYMENT_GUIDE.md "Going further: backups").

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKUP="${1:?Usage: ./scripts/restore.sh <backups/pg-*.sql.gz> [--yes]}"
ASSUME_YES=0
[ "${2:-}" = "--yes" ] && ASSUME_YES=1
[ -f "$BACKUP" ] || { echo "restore: file not found: $BACKUP" >&2; exit 1; }
[ -f .env ] || { echo "restore: .env not found" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

: "${POSTGRES_USER:?POSTGRES_USER missing in .env}"
: "${POSTGRES_DB:=pipelineiq}"

if [ "$ASSUME_YES" -ne 1 ]; then
  echo "WARNING: this REPLACES database '$POSTGRES_DB' with '$BACKUP'."
  printf 'Type the database name to continue: '
  read -r answer
  [ "$answer" = "$POSTGRES_DB" ] || { echo "restore: aborted."; exit 1; }
fi

echo "[restore] stopping writers..."
docker compose stop api sse-service worker-critical worker-default worker-bulk worker-gemini worker-streaming celery-beat flower 2>/dev/null || true

echo "[restore] dropping + recreating database..."
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$POSTGRES_DB' AND pid <> pg_backend_pid();"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"

echo "[restore] loading dump..."
gunzip -c "$BACKUP" | docker compose exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1

echo "[restore] aligning schema (alembic upgrade head)..."
docker compose exec -T api python -m alembic upgrade head

echo "[restore] restarting stack..."
docker compose up -d
sleep 15
curl -sf http://localhost/health >/dev/null \
  && echo "[restore] done — /health OK" \
  || { echo "[restore] WARNING: /health not OK yet; run 'make status' and check logs." >&2; exit 1; }
