-- Immutable accepted index versions, separate from transport inbox receipts.
CREATE TABLE gis_geometry_versions (
 resource_id uuid NOT NULL, revision bigint NOT NULL, event_id uuid NOT NULL UNIQUE,
 payload jsonb, payload_hash text NOT NULL, active boolean NOT NULL,
 observed_at timestamptz, geom public.geometry(Geometry,4326),
 PRIMARY KEY(resource_id,revision));
CREATE INDEX gis_geometry_versions_geom ON gis_geometry_versions USING gist(geom);
CREATE INDEX gis_geometry_versions_observed ON gis_geometry_versions(observed_at,resource_id,revision);
CREATE TABLE gis_geometry_heads (
 resource_id uuid PRIMARY KEY, revision bigint NOT NULL,
 FOREIGN KEY(resource_id,revision) REFERENCES gis_geometry_versions(resource_id,revision));
GRANT SELECT,INSERT ON gis_geometry_versions TO lzo_gis_app;
GRANT SELECT,INSERT,UPDATE ON gis_geometry_heads TO lzo_gis_app;
UPDATE store_schema_version SET version=3 WHERE version=2;
