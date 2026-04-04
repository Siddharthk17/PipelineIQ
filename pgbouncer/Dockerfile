FROM postgres:15-alpine

RUN apk add --no-cache pgbouncer su-exec

COPY entrypoint.sh /usr/local/bin/pgbouncer-entrypoint.sh
RUN chmod +x /usr/local/bin/pgbouncer-entrypoint.sh

EXPOSE 5432

ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint.sh"]
