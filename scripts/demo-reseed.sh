#!/usr/bin/env bash
#
# Nightly demo reseed: restores the public demo account to its pristine
# showcase state (50 pipelines, mixed outcomes, files, schedules).
#
# Wipes ONLY the demo account's runs/schedules/files — never other users.
# Installs into cron via scripts/bootstrap.sh (02:30 daily).
#
# Usage: ./scripts/demo-reseed.sh [--reset]
#   --reset is always passed by this script (full nightly restore).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[reseed $(date -u +%FT%TZ)] starting demo reseed"
docker compose exec -T api python -m backend.scripts.seed_showcase --reset
echo "[reseed $(date -u +%FT%TZ)] done"
