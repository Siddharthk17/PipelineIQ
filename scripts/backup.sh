#!/usr/bin/env bash
#
# Nightly PostgreSQL backup: pg_dump (custom format) + gzip into ./backups,
# 7-day retention. Run by cron (installed via scripts/bootstrap.sh, 02:00
# daily). Restore with: gunzip -c <file> | docker compose exec -T postgres \
#   psql -U "$POSTGRES_USER" "$POSTGRES_DB"
#
# Usage: ./scripts/backup.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "backup: .env not found" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

: "${POSTGRES_USER:?POSTGRES_USER missing in .env}"
: "${POSTGRES_DB:=pipelineiq}"

STAMP="$(date +%F-%H%M)"
OUT_DIR="$ROOT/backups"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/pg-$STAMP.sql.gz"

echo "[backup $(date -u +%FT%TZ)] dumping to $OUT"
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc | gzip > "$OUT"
echo "[backup] wrote $(du -h "$OUT" | cut -f1): $OUT"

find "$OUT_DIR" -name 'pg-*.sql.gz' -mtime +7 -delete
echo "[backup] retention: $(ls "$OUT_DIR"/pg-*.sql.gz 2>/dev/null | wc -l) backups kept"
