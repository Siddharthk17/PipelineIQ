#!/bin/sh
set -eu

PG_HBA="/var/lib/postgresql/data/pg_hba.conf"
REPL_USER="${REPLICATION_USER:-replicator}"

# Keep this init hook side-effect only; do not re-enter docker-entrypoint here.
grep -qxF "host replication ${REPL_USER} samenet scram-sha-256" "$PG_HBA" \
  || echo "host replication ${REPL_USER} samenet scram-sha-256" >> "$PG_HBA"
