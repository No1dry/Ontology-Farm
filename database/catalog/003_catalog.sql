CREATE TABLE catalog_versions (
    resource_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    event_id uuid NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('model','document')),
    payload jsonb NOT NULL,
    payload_hash text NOT NULL,
    PRIMARY KEY(resource_id,revision)
);
CREATE TABLE catalog_heads (
    resource_id uuid PRIMARY KEY,
    revision bigint NOT NULL,
    FOREIGN KEY(resource_id,revision) REFERENCES catalog_versions(resource_id,revision)
);
GRANT SELECT,INSERT ON catalog_versions TO lzo_catalog_app;
GRANT SELECT,INSERT,UPDATE ON catalog_heads TO lzo_catalog_app;
UPDATE store_schema_version SET version=3 WHERE version=2;
