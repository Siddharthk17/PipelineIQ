#!/bin/sh
set -eu

DB_HOST="${DATABASES_HOST:-db}"
DB_PORT="${DATABASES_PORT:-5432}"
DB_NAME="${DATABASES_DBNAME:-pipelineiq}"
DB_USER="${DATABASES_USER:-pipelineiq}"
DB_PASSWORD="${DATABASES_PASSWORD:-}"
DB_CLIENT_ALIAS_USER="${DATABASES_CLIENT_ALIAS_USER:-pipelineiq_user}"

LISTEN_PORT="${PGBOUNCER_LISTEN_PORT:-5432}"
POOL_MODE="${PGBOUNCER_POOL_MODE:-transaction}"
MAX_CLIENT_CONN="${PGBOUNCER_MAX_CLIENT_CONN:-1000}"
DEFAULT_POOL_SIZE="${PGBOUNCER_DEFAULT_POOL_SIZE:-25}"
MIN_POOL_SIZE="${PGBOUNCER_MIN_POOL_SIZE:-5}"
RESERVE_POOL_SIZE="${PGBOUNCER_RESERVE_POOL_SIZE:-5}"
SERVER_IDLE_TIMEOUT="${PGBOUNCER_SERVER_IDLE_TIMEOUT:-600}"
CLIENT_IDLE_TIMEOUT="${PGBOUNCER_CLIENT_IDLE_TIMEOUT:-0}"
LOG_POOLER_ERRORS="${PGBOUNCER_LOG_POOLER_ERRORS:-1}"

mkdir -p /etc/pgbouncer /var/run/pgbouncer
chown -R postgres:postgres /etc/pgbouncer /var/run/pgbouncer

cat > /etc/pgbouncer/pgbouncer.ini <<EOF
[databases]
${DB_NAME} = host=${DB_HOST} port=${DB_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASSWORD}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = ${LISTEN_PORT}
auth_type = trust
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = ${POOL_MODE}
max_client_conn = ${MAX_CLIENT_CONN}
default_pool_size = ${DEFAULT_POOL_SIZE}
min_pool_size = ${MIN_POOL_SIZE}
reserve_pool_size = ${RESERVE_POOL_SIZE}
server_idle_timeout = ${SERVER_IDLE_TIMEOUT}
client_idle_timeout = ${CLIENT_IDLE_TIMEOUT}
ignore_startup_parameters = extra_float_digits
log_pooler_errors = ${LOG_POOLER_ERRORS}
log_connections = 0
log_disconnections = 0
admin_users = ${DB_USER}
EOF

cat > /etc/pgbouncer/userlist.txt <<EOF
"${DB_USER}" "${DB_PASSWORD}"
EOF
if [ "${DB_CLIENT_ALIAS_USER}" != "${DB_USER}" ]; then
  printf '"%s" "%s"\n' "${DB_CLIENT_ALIAS_USER}" "${DB_PASSWORD}" >> /etc/pgbouncer/userlist.txt
fi

chown postgres:postgres /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt
chmod 600 /etc/pgbouncer/userlist.txt

exec su-exec postgres pgbouncer /etc/pgbouncer/pgbouncer.ini
