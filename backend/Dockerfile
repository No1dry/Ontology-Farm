FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home lzo \
    && mkdir -p /var/lib/lzo/artifacts \
    && chown -R lzo:lzo /var/lib/lzo
COPY backend/ ./backend/
COPY database/alembic.ini ./database/alembic.ini
COPY database/migrations/ ./database/migrations/
COPY database/fuseki/config.ttl ./database/fuseki/config.ttl
COPY infra/versions.lock.json ./infra/versions.lock.json
# Existing API tests also consume shared contracts, fixtures and helper scripts.
COPY contracts/ ./contracts/
COPY tests/fixtures/ ./tests/fixtures/
COPY scripts/ ./scripts/
COPY frontend/src/features/studio/fixtures/ ./frontend/src/features/studio/fixtures/
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend/src
USER lzo
EXPOSE 8000
CMD ["uvicorn", "lzo.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
