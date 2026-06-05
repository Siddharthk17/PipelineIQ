FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY server.js ./

ENV PORT=1234
EXPOSE 1234

CMD ["node", "server.js"]
