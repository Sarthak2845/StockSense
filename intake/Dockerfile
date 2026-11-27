# Build context is the repo root (see docker-compose.yml) so the shared
# contract in docs/contracts/ can be copied next to the service.
FROM node:20-alpine
ENV NODE_ENV=production
WORKDIR /app/intake

COPY intake/package.json intake/package-lock.json ./
RUN npm ci --omit=dev

COPY docs/contracts /app/docs/contracts
COPY intake/src ./src

USER node
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD wget -qO- http://127.0.0.1:3000/health || exit 1
CMD ["node", "src/server.js"]
