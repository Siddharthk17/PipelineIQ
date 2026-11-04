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

ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint.sh"]
