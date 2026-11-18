#!/usr/bin/env bash
#
# Nightly demo reseed: restores the public demo account to its pristine
# showcase state (50 pipelines, mixed outcomes, files, schedules).
#
# OPT-IN ONLY (M-002/M-003): does nothing unless PIPELINEIQ_SEED_DEMO=1,
# and --reset always passes the explicit demo target so only the demo
# account's runs/schedules/files (+ demo-owned pipeline versions) are
# wiped — never other users' data. Installs into cron via
# scripts/bootstrap.sh (02:30 daily) only when the same flag is set.
#
# Usage: ./scripts/demo-reseed.sh [--reset]
#   --reset is always passed by this script (full nightly restore).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "${PIPELINEIQ_SEED_DEMO:-0}" != "1" ]; then
  echo "[reseed $(date -u +%FT%TZ)] refusing: PIPELINEIQ_SEED_DEMO!=1 (opt-in required)" >&2
  exit 1
fi

echo "[reseed $(date -u +%FT%TZ)] starting demo reseed"
docker compose exec -T api python -m backend.scripts.seed_showcase --reset --target demo@pipelineiq.app
echo "[reseed $(date -u +%FT%TZ)] done"
