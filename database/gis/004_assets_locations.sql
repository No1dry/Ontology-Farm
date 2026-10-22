CREATE TABLE gis_spatial_assets (
 asset_id uuid PRIMARY KEY, resource_id uuid NOT NULL, event_id uuid NOT NULL UNIQUE,
 revision bigint NOT NULL, artifact_version_id uuid NOT NULL, payload jsonb NOT NULL,
 footprint public.geometry(Geometry,4326) NOT NULL);
CREATE INDEX gis_spatial_assets_footprint ON gis_spatial_assets USING gist(footprint);
CREATE INDEX gis_spatial_assets_resource ON gis_spatial_assets(resource_id,asset_id);
CREATE TABLE gis_location_observations (
 observation_id uuid PRIMARY KEY, resource_id uuid NOT NULL, event_id uuid NOT NULL UNIQUE,
 revision bigint NOT NULL, observed_at timestamptz NOT NULL, payload jsonb NOT NULL,
 position public.geometry(Point,4326) NOT NULL);
CREATE INDEX gis_location_observations_time ON gis_location_observations(observed_at,resource_id,observation_id);
CREATE INDEX gis_location_observations_geom ON gis_location_observations USING gist(position);
ALTER TABLE gis_geometry_versions ADD CONSTRAINT gis_geometry_valid CHECK
 (geom IS NULL OR (public.ST_IsValid(geom) AND NOT public.ST_IsEmpty(geom)
 AND public.ST_XMin(public.Box3D(geom))>=-180 AND public.ST_XMax(public.Box3D(geom))<=180
 AND public.ST_YMin(public.Box3D(geom))>=-90 AND public.ST_YMax(public.Box3D(geom))<=90));
GRANT SELECT,INSERT ON gis_spatial_assets,gis_location_observations TO lzo_gis_app;
UPDATE store_schema_version SET version=4 WHERE version=3;
