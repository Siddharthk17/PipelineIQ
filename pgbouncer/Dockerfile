FROM alpine:3.20

RUN apk add --no-cache \
    su-exec \
    pgbouncer \
    postgresql15-client

COPY entrypoint.sh /usr/local/bin/pgbouncer-entrypoint.sh
RUN chmod +x /usr/local/bin/pgbouncer-entrypoint.sh

EXPOSE 5432

ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint.sh"]
