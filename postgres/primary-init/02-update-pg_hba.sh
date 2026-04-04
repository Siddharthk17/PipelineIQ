#!/bin/sh
set -eu

echo "host replication replicator 0.0.0.0/0 trust" >> /var/lib/postgresql/data/pg_hba.conf
echo "host replication replicator ::0/0 trust" >> /var/lib/postgresql/data/pg_hba.conf

exec docker-entrypoint.sh postgres "$@"
