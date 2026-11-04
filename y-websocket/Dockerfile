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

CMD ["node", "server.js"]
