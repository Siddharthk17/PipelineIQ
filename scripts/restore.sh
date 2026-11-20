#!/usr/bin/env bash
#
# Restore a PostgreSQL backup created by scripts/backup.sh.
#
# This is the drill half of the backup story — a backup you have never
# restored is a rumor. Practice on a scratch clone before you need it.
#
# Usage:
#   ./scripts/restore.sh backups/pg-2026-01-02-0200.sql.gz --yes
#   ./scripts/restore.sh backups/pg-2026-01-02-0200.sql.gz --yes --with-objects backups/objects-2026-01-02-0200
#
# What it does:
#   1. Refuses to run without --yes unless you confirm interactively
#      (restore REPLACES the live database). --yes is required in
#      non-interactive use; without a TTY there is no fallback.
#   2. Validates the dump BEFORE touching the live database: gzip
#      integrity plus a format check (plain SQL is loaded with psql,
#      legacy -Fc custom archives with pg_restore). Nothing is dropped
#      until the dump proves loadable. An --with-objects directory is
#      validated up front too (manifest or archive present, mc available
#      when bucket mirrors are included).
#   3. Stops writers (api, workers, beat, sse) so nothing writes mid-restore.
#   4. Drops + recreates the database, loads the dump, runs alembic upgrade
#      head via a one-off container (the api service is stopped at this
#      point, so exec-ing into it would fail).
#   5. Restores objects (buckets via `mc mirror`, local dir from tarball)
#      when --with-objects was given — AFTER the DB load so presigned/
#      referenced keys line up with restored rows.
#   6. Restarts the stack and verifies /health.
#
# MinIO objects are NOT part of pg_dump. If uploads/outputs matter, back
# them up with scripts/backup.sh (objects-<stamp>/) and pass that
# directory via --with-objects (see DEPLOYMENT_GUIDE.md
# "Going further: backups").

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKUP="${1:?Usage: ./scripts/restore.sh <backups/pg-*.sql.gz> [--yes] [--with-objects DIR] [--dry-run]}"
ASSUME_YES=0
OBJECTS_DIR=""
DRY_RUN=0
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --with-objects) OBJECTS_DIR="${2:?--with-objects needs a directory}"; shift ;;
    --with-objects=*) OBJECTS_DIR="${1#--with-objects=}" ;;
    *) echo "restore: unknown argument: $1" >&2; exit 1 ;;
  esac
  shift
done
[ -f "$BACKUP" ] || { echo "restore: file not found: $BACKUP" >&2; exit 1; }
[ -f .env ] || { echo "restore: .env not found" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

: "${POSTGRES_USER:?POSTGRES_USER missing in .env}"
: "${POSTGRES_DB:=pipelineiq}"
: "${MC_BIN:=mc}"
: "${MC_ALIAS:=pipelineiq-minio}"
: "${OBJECT_RESTORE_DIR:=${OBJECT_SOURCE_DIR:-}}"

if [ "$ASSUME_YES" -ne 1 ]; then
  if [ ! -t 0 ]; then
    echo "restore: refusing to run without --yes on a non-interactive shell." >&2
    exit 1
  fi
  echo "WARNING: this REPLACES database '$POSTGRES_DB' with '$BACKUP'."
  printf 'Type the database name to continue: '
  read -r answer
  [ "$answer" = "$POSTGRES_DB" ] || { echo "restore: aborted."; exit 1; }
fi

echo "[restore] validating dump before touching the live database..."
gunzip -t "$BACKUP" || { echo "restore: $BACKUP failed gzip integrity check; aborting before DROP." >&2; exit 1; }

DUMP_FORMAT="plain"
if gunzip -c "$BACKUP" 2>/dev/null | head -c 5 | grep -q "PGDMP"; then
  DUMP_FORMAT="custom"
fi
echo "[restore] detected dump format: $DUMP_FORMAT"

if [ "$DUMP_FORMAT" = "custom" ]; then
  # Legacy -Fc archives (pre-plain-format backups): pg_restore must read
  # them — psql cannot. --list validates the archive without loading it.
  gunzip -c "$BACKUP" | docker compose exec -T postgres \
    pg_restore --list -d postgres >/dev/null \
    || { echo "restore: pg_restore cannot read $BACKUP; aborting before DROP." >&2; exit 1; }
else
  # Plain SQL: require non-empty decoded output with at least one SQL
  # statement terminator so an empty/corrupt dump never wipes the live DB.
  DECODED_BYTES="$(gunzip -c "$BACKUP" | wc -c)"
  if [ "$DECODED_BYTES" -lt 16 ]; then
    echo "restore: $BACKUP decodes to $DECODED_BYTES bytes; aborting before DROP." >&2
    exit 1
  fi
  gunzip -c "$BACKUP" | grep -q ";" \
    || { echo "restore: $BACKUP contains no SQL statements; aborting before DROP." >&2; exit 1; }
fi
echo "[restore] dump validated; proceeding."

# --- M-137: validate the object backup BEFORE stopping writers ---
RESTORE_BUCKETS=""
RESTORE_LOCAL_TARBALL=""
if [ -n "$OBJECTS_DIR" ]; then
  [ -d "$OBJECTS_DIR" ] || {
    echo "restore: --with-objects dir not found: $OBJECTS_DIR" >&2; exit 1
  }
  if [ -f "$OBJECTS_DIR/objects-manifest.txt" ]; then
    RESTORE_BUCKETS="$OBJECTS_DIR"
    command -v "$MC_BIN" >/dev/null 2>&1 || {
      echo "restore: $MC_BIN not found but bucket mirrors need it" >&2; exit 1
    }
    [ -n "${S3_ENDPOINT_URL:-}" ] || {
      echo "restore: S3_ENDPOINT_URL unset but bucket mirrors need it" >&2; exit 1
    }
  fi
  RESTORE_LOCAL_TARBALL="$(ls "$OBJECTS_DIR"/objects-local-*.tar.gz 2>/dev/null | head -1 || true)"
  if [ -z "$RESTORE_LOCAL_TARBALL" ]; then
    # backup.sh writes the local tarball next to the objects dir
    # (objects-local-<stamp>.tar.gz); accept that layout too.
    SIBLING="$(dirname "$OBJECTS_DIR")/objects-local-$(basename "$OBJECTS_DIR" | sed 's/^objects-//').tar.gz"
    [ -f "$SIBLING" ] && RESTORE_LOCAL_TARBALL="$SIBLING"
  fi
  if [ -z "$RESTORE_BUCKETS" ] && [ -z "$RESTORE_LOCAL_TARBALL" ]; then
    echo "restore: $OBJECTS_DIR has no objects-manifest.txt nor objects-local-*.tar.gz" >&2
    exit 1
  fi
  [ -z "$RESTORE_LOCAL_TARBALL" ] || gunzip -t "$RESTORE_LOCAL_TARBALL" || {
    echo "restore: $RESTORE_LOCAL_TARBALL failed integrity check; aborting before DROP." >&2
    exit 1
  }
  echo "[restore] object backup validated: ${RESTORE_BUCKETS:-no buckets} ${RESTORE_LOCAL_TARBALL:-no local tarball}"
fi

# C2-013: --dry-run drills validation without touching anything. The only
# safe restore rehearsal used to be the destructive restore itself; a
# dry run proves the dump + objects load while writers keep running.
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[restore] DRY RUN: dump ($DUMP_FORMAT) + objects validate. Plan:"
  echo "  - stop writers: api sse-service worker-* celery-beat flower"
  echo "  - drop + recreate database '$POSTGRES_DB'"
  echo "  - load '$BACKUP' ($DUMP_FORMAT)"
  echo "  - alembic upgrade head"
  echo "  - objects: ${RESTORE_BUCKETS:-none} ${RESTORE_LOCAL_TARBALL:-none}"
  echo "  - restart stack, verify /health"
  echo "[restore] DRY RUN ok: nothing was stopped, dropped, or loaded."
  exit 0
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

echo "[restore] loading dump ($DUMP_FORMAT format)..."
if [ "$DUMP_FORMAT" = "custom" ]; then
  gunzip -c "$BACKUP" | docker compose exec -T postgres \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q
else
  gunzip -c "$BACKUP" | docker compose exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1
fi

echo "[restore] aligning schema (alembic upgrade head)..."
docker compose run --rm api python -m alembic upgrade head

# --- M-137: restore objects AFTER the DB load ---
if [ -n "$OBJECTS_DIR" ]; then
  if [ -n "$RESTORE_BUCKETS" ]; then
    "$MC_BIN" alias set "$MC_ALIAS" "$S3_ENDPOINT_URL" \
      "${S3_ACCESS_KEY:-}" "${S3_SECRET_KEY:-}" >/dev/null
    for bucket_dir in "$RESTORE_BUCKETS"/*/; do
      [ -d "$bucket_dir" ] || continue
      bucket="$(basename "$bucket_dir")"
      echo "[restore] mirroring objects -> bucket $bucket"
      "$MC_BIN" mirror --overwrite "$bucket_dir" "$MC_ALIAS/$bucket"
    done
  fi
  if [ -n "$RESTORE_LOCAL_TARBALL" ]; then
    [ -n "$OBJECT_RESTORE_DIR" ] || {
      echo "restore: set OBJECT_SOURCE_DIR/OBJECT_RESTORE_DIR for the local tarball" >&2
      exit 1
    }
    echo "[restore] extracting $RESTORE_LOCAL_TARBALL -> $OBJECT_RESTORE_DIR"
    mkdir -p "$OBJECT_RESTORE_DIR"
    tar -xzf "$RESTORE_LOCAL_TARBALL" -C "$ROOT"
  fi
  echo "[restore] objects restored from $OBJECTS_DIR"
fi

echo "[restore] restarting stack..."
docker compose up -d
sleep 15
curl -sf http://localhost/health >/dev/null \
  && echo "[restore] done — /health OK" \
  || { echo "restore: WARNING: /health not OK yet; run 'make status' and check logs." >&2; exit 1; }
