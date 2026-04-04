#!/bin/sh
set -eu

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PRIMARY_HOST="${PRIMARY_HOST:-postgres}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-${POSTGRES_PASSWORD:-}}"

if [ -z "${REPLICATION_PASSWORD}" ]; then
  echo "REPLICATION_PASSWORD (or POSTGRES_PASSWORD) must be set for replica bootstrap" >&2
  exit 1
fi

mkdir -p "${PGDATA}"
chown -R postgres:postgres "${PGDATA}"
chmod 700 "${PGDATA}"

if [ ! -s "${PGDATA}/PG_VERSION" ]; then
  echo "Bootstrapping replica from primary host ${PRIMARY_HOST}..."
  rm -rf "${PGDATA:?}/"*
  export PGPASSWORD="${REPLICATION_PASSWORD}"
  until su-exec postgres pg_basebackup \
    -h "${PRIMARY_HOST}" \
    -D "${PGDATA}" \
    -U "${REPLICATION_USER}" \
    -Fp \
    -Xs \
    -P \
    -R
  do
    echo "Waiting for primary to become ready for replication..."
    sleep 2
  done
fi

exec su-exec postgres postgres \
  -D "${PGDATA}" \
  -c hot_standby=on \
  -c primary_conninfo="host=${PRIMARY_HOST} user=${REPLICATION_USER} password=${REPLICATION_PASSWORD}"
