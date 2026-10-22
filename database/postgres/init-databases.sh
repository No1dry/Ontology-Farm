#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname postgres -v ON_ERROR_STOP=1 \
  --set owner_password="$LZO_OWNER_PASSWORD" --set app_password="$LZO_APP_PASSWORD" \
  --set gis_owner="$LZO_GIS_OWNER_PASSWORD" --set gis_app="$LZO_GIS_APP_PASSWORD" \
  --set timeseries_owner="$LZO_TIMESERIES_OWNER_PASSWORD" --set timeseries_app="$LZO_TIMESERIES_APP_PASSWORD" \
  --set catalog_owner="$LZO_CATALOG_OWNER_PASSWORD" --set catalog_app="$LZO_CATALOG_APP_PASSWORD" \
  --set keycloak_password="$KEYCLOAK_DB_PASSWORD" <<'SQL'
CREATE ROLE lzo_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE lzo_app LOGIN PASSWORD :'app_password';
CREATE ROLE lzo_gis_owner LOGIN PASSWORD :'gis_owner';
CREATE ROLE lzo_gis_app LOGIN PASSWORD :'gis_app';
CREATE ROLE lzo_timeseries_owner LOGIN PASSWORD :'timeseries_owner';
CREATE ROLE lzo_timeseries_app LOGIN PASSWORD :'timeseries_app';
CREATE ROLE lzo_catalog_owner LOGIN PASSWORD :'catalog_owner';
CREATE ROLE lzo_catalog_app LOGIN PASSWORD :'catalog_app';
CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_password';
CREATE DATABASE lzo OWNER lzo_owner;
CREATE DATABASE lzo_gis OWNER lzo_gis_owner;
CREATE DATABASE lzo_timeseries OWNER lzo_timeseries_owner;
CREATE DATABASE lzo_catalog OWNER lzo_catalog_owner;
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE lzo FROM PUBLIC;
REVOKE ALL ON DATABASE lzo_gis FROM PUBLIC;
REVOKE ALL ON DATABASE lzo_timeseries FROM PUBLIC;
REVOKE ALL ON DATABASE lzo_catalog FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
GRANT CONNECT ON DATABASE lzo TO lzo_owner,lzo_app;
GRANT CONNECT ON DATABASE lzo_gis TO lzo_gis_owner,lzo_gis_app;
GRANT CONNECT ON DATABASE lzo_timeseries TO lzo_timeseries_owner,lzo_timeseries_app;
GRANT CONNECT ON DATABASE lzo_catalog TO lzo_catalog_owner,lzo_catalog_app;
GRANT CONNECT ON DATABASE keycloak TO keycloak;
\connect lzo
CREATE EXTENSION IF NOT EXISTS postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_app;
\connect lzo_gis
CREATE EXTENSION IF NOT EXISTS postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_gis_app;
\connect lzo_timeseries
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_timeseries_app;
\connect lzo_catalog
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_catalog_app;
SQL
