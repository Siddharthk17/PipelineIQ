FROM node:20.19.0-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev && chown -R node:node /app

COPY server.js ./

RUN chown node:node /app/server.js

ENV PORT=1234
EXPOSE 1234

# Drop privileges: the server only needs its port and Redis, both reachable
# as an unprivileged user (compose mounts ./y-websocket:/app plus a named
# node_modules volume so runtime `npm install` never depends on host perms).
USER node

# M-187: image-level health probe (compose overrides per-service). The relay
# is a raw WebSocket server with no HTTP endpoint, so a TCP connect is the
# cheapest correct probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD node -e "require('net').createConnection(1234,'localhost').on('connect',function(){process.exit(0)}).on('error',function(){process.exit(1)})"

CMD ["node", "server.js"]
