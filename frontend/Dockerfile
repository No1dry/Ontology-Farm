FROM node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS dependencies
WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY contracts/openapi.json /workspace/contracts/openapi.json

FROM dependencies AS test
CMD ["sh", "-c", "npm test && npm run typecheck && npm run api:check && npm run build"]

FROM dependencies AS build
RUN npm run typecheck && npm run api:check && npm run build

FROM nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284
COPY --from=build /workspace/frontend/dist /usr/share/nginx/html
COPY infra/proxy/nginx.conf /etc/nginx/nginx.conf
EXPOSE 8443
