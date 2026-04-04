#!/bin/sh
set -eu

PG_HBA="/var/lib/postgresql/data/pg_hba.conf"

# Keep this init hook side-effect only; do not re-enter docker-entrypoint here.
grep -qxF "host replication replicator 0.0.0.0/0 trust" "$PG_HBA" \
  || echo "host replication replicator 0.0.0.0/0 trust" >> "$PG_HBA"
grep -qxF "host replication replicator ::0/0 trust" "$PG_HBA" \
  || echo "host replication replicator ::0/0 trust" >> "$PG_HBA"
