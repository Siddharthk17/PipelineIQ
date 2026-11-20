#!/usr/bin/env bash
#
# Nightly PostgreSQL backup: pg_dump (plain SQL) + gzip into ./backups,
# 7-day retention. Run by cron (installed via scripts/bootstrap.sh, 02:00
# daily). Restore with: gunzip -c <file> | docker compose exec -T postgres \
#   psql -U "$POSTGRES_USER" "$POSTGRES_DB"
#
# Plain SQL (not -Fc custom) is deliberate: the documented restore path
# pipes the dump into psql, which cannot read custom-format archives
# (those require pg_restore). See restore.sh, which validates the dump
# BEFORE dropping anything and accepts either format.
#
# Usage: ./scripts/backup.sh [--objects-only]
#
# Object backup (M-137): pg_dump covers the database only — uploads,
# pipeline outputs and WASM modules live in MinIO/S3 buckets (or the
# local data dir when STORAGE_TYPE=local) and need their own backup.
# After the PG dump this script also captures objects:
#
#   * MinIO/S3 mode: `mc mirror` of $S3_BUCKET (+ $OBJECT_EXTRA_BUCKETS,
#     space-separated) into backups/objects-<stamp>/<bucket>/, when the
#     `mc` client is available and S3_ENDPOINT_URL is set. Alias
#     credentials come from S3_ACCESS_KEY/S3_SECRET_KEY.
#   * Local mode: when OBJECT_SOURCE_DIR is set (e.g. ./uploads), it is
#     tarred to backups/objects-local-<stamp>.tar.gz.
#
# Knobs (environment or .env; all optional):
#   OBJECT_BACKUP_ENABLED=auto|1|0  (auto = enabled when a source exists)
#   MC_BIN=mc  MC_ALIAS=pipelineiq-minio
#   OBJECT_SOURCE_DIR=  OBJECT_EXTRA_BUCKETS=
#   OBJECT_BACKUP_ONLY=1  (skip the PG dump; objects only — also handy
#     for testing without a live compose stack)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OBJECTS_ONLY=0
[ "${1:-}" = "--objects-only" ] && OBJECTS_ONLY=1

[ -f .env ] || { echo "backup: .env not found" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

: "${POSTGRES_USER:?POSTGRES_USER missing in .env}"
: "${POSTGRES_DB:=pipelineiq}"
: "${STAMP:=$(date +%F-%H%M)}"
: "${OBJECT_BACKUP_ENABLED:=auto}"
: "${MC_BIN:=mc}"
: "${MC_ALIAS:=pipelineiq-minio}"
: "${S3_BUCKET:=pipelineiq-uploads}"
: "${OBJECT_EXTRA_BUCKETS:=}"
: "${OBJECT_SOURCE_DIR:=}"

OUT_DIR="$ROOT/backups"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/pg-$STAMP.sql.gz"

backup_minio_objects() {
  # Mirror each bucket with the MinIO client. Returns 0 when nothing
  # was configured (caller decides whether that is an error).
  command -v "$MC_BIN" >/dev/null 2>&1 || {
    echo "[backup] $MC_BIN not found; skipping bucket mirror" >&2
    return 2
  }
  [ -n "${S3_ENDPOINT_URL:-}" ] || {
    echo "[backup] S3_ENDPOINT_URL unset; skipping bucket mirror" >&2
    return 2
  }
  "$MC_BIN" alias set "$MC_ALIAS" "$S3_ENDPOINT_URL" \
    "${S3_ACCESS_KEY:-}" "${S3_SECRET_KEY:-}" >/dev/null
  dest="$OUT_DIR/objects-$STAMP"
  mkdir -p "$dest"
  # shellcheck disable=SC2086
  for bucket in "$S3_BUCKET" $OBJECT_EXTRA_BUCKETS; do
    [ -n "$bucket" ] || continue
    echo "[backup] mirroring bucket $bucket -> $dest/$bucket"
    "$MC_BIN" mirror --overwrite "$MC_ALIAS/$bucket" "$dest/$bucket"
  done
  {
    echo "stamp: $(date -u +%FT%TZ)"
    echo "endpoint: $S3_ENDPOINT_URL"
    echo "buckets: $S3_BUCKET $OBJECT_EXTRA_BUCKETS"
    find "$dest" -type f | wc -l
  } > "$dest/objects-manifest.txt"
  echo "[backup] object backup: $(find "$dest" -type f | wc -l) files in $dest"
  return 0
}

backup_local_objects() {
  [ -n "$OBJECT_SOURCE_DIR" ] || return 2
  [ -d "$OBJECT_SOURCE_DIR" ] || {
    echo "[backup] OBJECT_SOURCE_DIR=$OBJECT_SOURCE_DIR not a directory" >&2
    return 1
  }
  local_out="$OUT_DIR/objects-local-$STAMP.tar.gz"
  echo "[backup] archiving $OBJECT_SOURCE_DIR -> $local_out"
  tar -czf "$local_out" -C "$ROOT" "$OBJECT_SOURCE_DIR"
  gunzip -t "$local_out" \
    || { echo "backup: integrity check failed for $local_out" >&2; exit 1; }
  echo "[backup] wrote $(du -h "$local_out" | cut -f1): $local_out"
  return 0
}

if [ "$OBJECTS_ONLY" -eq 0 ]; then
echo "[backup $(date -u +%FT%TZ)] dumping to $OUT"
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "$OUT"
gunzip -t "$OUT" || { echo "backup: integrity check failed for $OUT" >&2; exit 1; }
echo "[backup] wrote $(du -h "$OUT" | cut -f1): $OUT"

find "$OUT_DIR" -name 'pg-*.sql.gz' -mtime +7 -delete
echo "[backup] retention: $(ls "$OUT_DIR"/pg-*.sql.gz 2>/dev/null | wc -l) backups kept"
fi

# --- M-137: object backup (never fails the PG backup) ---
OBJECTS_DONE=0
if [ "$OBJECT_BACKUP_ENABLED" = "1" ] \
  || { [ "$OBJECT_BACKUP_ENABLED" = "auto" ] \
    && { [ -n "$OBJECT_SOURCE_DIR" ] || [ -n "${S3_ENDPOINT_URL:-}" ]; }; }; then
  MINIO_RC=0
  LOCAL_RC=0
  backup_minio_objects && OBJECTS_DONE=1 || MINIO_RC=$?
  backup_local_objects && OBJECTS_DONE=1 || LOCAL_RC=$?
  if [ "$OBJECTS_DONE" -eq 0 ] && [ "$OBJECT_BACKUP_ENABLED" = "1" ]; then
    echo "backup: OBJECT_BACKUP_ENABLED=1 but no object source captured" >&2
    echo "backup: minio rc=$MINIO_RC local rc=$LOCAL_RC" >&2
    exit 1
  fi
else
  echo "[backup] object backup disabled (OBJECT_BACKUP_ENABLED=$OBJECT_BACKUP_ENABLED)"
fi
echo "[backup] done (objects: $([ "$OBJECTS_DONE" -eq 1 ] && echo captured || echo skipped))"
