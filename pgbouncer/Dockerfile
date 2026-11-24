FROM alpine:3.20.3

# Note: no USER line by design — the entrypoint writes config files, then
# drops privileges itself via `exec su-exec postgres pgbouncer ...`.

RUN apk add --no-cache \
    su-exec \
    pgbouncer \
    postgresql15-client

COPY entrypoint.sh /usr/local/bin/pgbouncer-entrypoint.sh
RUN chmod +x /usr/local/bin/pgbouncer-entrypoint.sh

EXPOSE 5432

# M-187: image-level health probe (compose overrides per-service).
# postgresql15-client provides pg_isready; no user flag so the probe does
# not depend on which database role is configured.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pg_isready -h 127.0.0.1 -p 5432 || exit 1

ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint.sh"]
