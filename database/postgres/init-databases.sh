#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname postgres \
  --set owner_password="$LZO_OWNER_PASSWORD" --set app_password="$LZO_APP_PASSWORD" \
  --set keycloak_password="$KEYCLOAK_DB_PASSWORD" <<'SQL'
CREATE ROLE lzo_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE lzo_app LOGIN PASSWORD :'app_password';
CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_password';
CREATE DATABASE lzo OWNER lzo_owner;
CREATE DATABASE keycloak OWNER keycloak;
\connect lzo
CREATE EXTENSION IF NOT EXISTS postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_app;
SQL
