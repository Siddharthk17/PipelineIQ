FROM nginx:1.25.5-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ /etc/nginx/conf.d/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
EXPOSE 80
# M-187: image-level health probe (compose overrides per-service).
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:80/health || exit 1
ENTRYPOINT ["/docker-entrypoint.sh"]
